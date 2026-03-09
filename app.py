"""
Flask app for support ticket dashboard.
View tickets, full conversation (customer + support), reply, manual create.
Customer identification & domain tagging, basic role-based access.
"""
import os
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv
from werkzeug.security import check_password_hash

load_dotenv()

from database import tickets, customers, agents, admins
from email_fetching import send_support_reply, normalize_msg_id, get_next_ticket_number

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-production")


def login_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in.", "error")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return inner


def get_customer_email_for_ticket(ticket):
    """Get the customer email for a ticket (from company's contacts)."""
    if not ticket:
        return None
    info = get_customer_info_for_ticket(ticket)
    return info.get("email") if info else None


def get_customer_info_for_ticket(ticket):
    """Get company name, domain, and contact email for a ticket (customer identification & domain tagging)."""
    if not ticket:
        return None
    company_id = ticket.get("company_id")
    customer_id = ticket.get("customer_id")
    if not company_id or not customer_id:
        return None
    customer_doc = customers.find_one({"_id": company_id})
    if not customer_doc:
        return None
    for c in customer_doc.get("contacts", []):
        if not isinstance(c, dict):
            continue
        if c.get("customer_id") == customer_id:
            return {
                "company_name": customer_doc.get("company_name") or company_id,
                "domain": customer_doc.get("domain") or "",
                "email": c.get("email"),
                "contact_id": customer_id,
            }
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("user_id"):
            return redirect(url_for("ticket_list"))
        return render_template("login.html")
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    if not email:
        flash("Email is required.", "error")
        return redirect(url_for("login"))
    user = admins.find_one({"email": email})
    role = "admin"
    if not user:
        user = agents.find_one({"email": email})
        role = "agent"
    # First-time setup: if no admins/agents in DB, allow any email as admin
    if not user and admins.count_documents({}) == 0 and agents.count_documents({}) == 0:
        user = {"_id": "setup", "email": email, "name": email.split("@")[0], "password_hash": None}
        role = "admin"
    if not user:
        flash("No user found with this email.", "error")
        return redirect(url_for("login"))
    if not user.get("is_active", True):
        flash("Account is inactive.", "error")
        return redirect(url_for("login"))
    ph = user.get("password_hash")
    if ph and not check_password_hash(ph, password):
        flash("Invalid password.", "error")
        return redirect(url_for("login"))
    session["user_id"] = str(user.get("_id"))
    session["user_email"] = user.get("email")
    session["user_name"] = user.get("name") or email
    session["role"] = role
    next_url = request.args.get("next") or url_for("ticket_list")
    return redirect(next_url)


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/")
def index():
    return redirect(url_for("ticket_list"))


@app.route("/tickets")
@login_required
def ticket_list():
    status = request.args.get("status", "").strip()
    domain = request.args.get("domain", "").strip()
    query = {}
    if status:
        query["status"] = status
    if domain:
        company = customers.find_one({"domain": domain})
        if company:
            query["company_id"] = company["_id"]
        else:
            query["company_id"] = "__no_match__"  # no such company, show no tickets
    ticket_list = list(tickets.find(query).sort("updated_at", -1))
    # Attach customer/domain info for each ticket (customer identification & domain tagging)
    for t in ticket_list:
        t["_customer_info"] = get_customer_info_for_ticket(t)
    # All domains for filter dropdown
    domains = list(customers.distinct("domain"))
    domains.sort()
    return render_template("tickets_list.html", tickets=ticket_list, domains=domains)


@app.route("/tickets/create", methods=["GET", "POST"])
@login_required
def ticket_create():
    if request.method == "GET":
        # Build list of (company_id, customer_id, label) for dropdown
        customer_choices = []
        for c in customers.find().sort("company_name", 1):
            if not isinstance(c, dict):
                continue
            name = c.get("company_name") or c.get("domain") or c.get("_id")
            for contact in c.get("contacts", []):
                if not isinstance(contact, dict):
                    continue
                email = contact.get("email") or "—"
                customer_choices.append({
                    "company_id": c["_id"],
                    "customer_id": contact.get("customer_id"),
                    "label": f"{name} — {email}",
                })
        return render_template("ticket_create.html", customer_choices=customer_choices)
    # POST: create ticket
    company_id = request.form.get("company_id", "").strip()
    customer_id = request.form.get("customer_id", "").strip()
    subject = request.form.get("subject", "").strip()
    issue = request.form.get("issue", "General Inquiry").strip() or "General Inquiry"
    priority = request.form.get("priority", "Medium").strip() or "Medium"
    body = request.form.get("body", "").strip()
    if not company_id or not customer_id:
        flash("Please select a customer.", "error")
        return redirect(url_for("ticket_create"))
    if not subject:
        flash("Subject is required.", "error")
        return redirect(url_for("ticket_create"))
    if not body:
        flash("Initial message is required.", "error")
        return redirect(url_for("ticket_create"))
    ticket_id = get_next_ticket_number()
    now = datetime.now(timezone.utc)
    initial_comment = {
        "comment_id": f"manual-{ticket_id}-{now.timestamp():.0f}",
        "message_id": f"manual-{ticket_id}-{now.timestamp():.0f}",
        "body": body,
        "created_at": now,
        "from_support": True,
    }
    tickets.insert_one({
        "_id": ticket_id,
        "thread_id": None,
        "company_id": company_id,
        "customer_id": customer_id,
        "subject": subject,
        "issue": issue,
        "status": "Open",
        "priority": priority,
        "created_at": now,
        "updated_at": now,
        "comments": [initial_comment],
    })
    flash(f"Ticket {ticket_id} created.", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/tickets/<ticket_id>")
@login_required
def ticket_detail(ticket_id):
    ticket = tickets.find_one({"_id": ticket_id})
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("ticket_list"))
    # Sort comments by time so order is always chronological (handles timezone/naive datetimes)
    def _comment_time(c):
        if not isinstance(c, dict):
            return 0.0
        t = c.get("created_at")
        if t is None:
            return 0.0
        if hasattr(t, "timestamp"):
            return t.timestamp()
        return 0.0
    comments = sorted([c for c in ticket.get("comments", []) if isinstance(c, dict)], key=_comment_time)
    customer_info = get_customer_info_for_ticket(ticket)
    return render_template(
        "ticket_detail.html",
        ticket=ticket,
        comments=comments,
        customer_info=customer_info
    )


@app.route("/tickets/<ticket_id>/reply", methods=["POST"])
@login_required
def ticket_reply(ticket_id):
    ticket = tickets.find_one({"_id": ticket_id})
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("ticket_list"))

    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Reply body is required.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    to_email = get_customer_email_for_ticket(ticket)
    if not to_email:
        flash("Customer email not found for this ticket.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    # Build thread headers from last comment so customer's reply stays in same ticket
    comments = ticket.get("comments", [])
    in_reply_to = None
    references = []
    if comments:
        last = comments[-1]
        if isinstance(last, dict):
            mid = last.get("message_id")
            if mid:
                in_reply_to = mid
                references = [c.get("message_id") for c in comments if isinstance(c, dict) and c.get("message_id")]
    if not in_reply_to and ticket.get("ack_message_id"):
        in_reply_to = ticket["ack_message_id"]
        references = [in_reply_to]

    message_id, reply_body = send_support_reply(
        ticket_id, to_email, body, in_reply_to=in_reply_to, references=references or None
    )
    if not message_id:
        flash("Failed to send reply email.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    # Store support reply in DB so full conversation is in one place
    now = datetime.now(timezone.utc)
    mid = normalize_msg_id(message_id)
    support_comment = {
        "comment_id": mid,
        "message_id": mid,
        "body": reply_body,
        "created_at": now,
        "from_support": True
    }
    tickets.update_one(
        {"_id": ticket_id},
        {
            "$push": {"comments": support_comment},
            "$set": {"updated_at": now}
        }
    )
    flash("Reply sent and saved to ticket.", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


ALLOWED_STATUSES = ("Open", "Pending", "Closed")


@app.route("/tickets/<ticket_id>/status", methods=["POST"])
@login_required
def ticket_update_status(ticket_id):
    ticket = tickets.find_one({"_id": ticket_id})
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("ticket_list"))
    new_status = (request.form.get("status") or "").strip()
    if new_status not in ALLOWED_STATUSES:
        flash("Invalid status.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))
    now = datetime.now(timezone.utc)
    tickets.update_one(
        {"_id": ticket_id},
        {"$set": {"status": new_status, "updated_at": now}}
    )
    flash(f"Ticket set to {new_status}.", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005, debug=True)
