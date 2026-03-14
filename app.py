"""
Flask app for support ticket dashboard.
View tickets, full conversation (customer + support), reply, manual create.
Customer identification & domain tagging, basic role-based access.
"""
import difflib
import logging
import os
import re
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from dotenv import load_dotenv
from werkzeug.security import check_password_hash

load_dotenv()

import google.generativeai as genai

from database import tickets, customers, agents, admins
from email_service import send_support_reply, normalize_msg_id, get_next_ticket_number

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-production")
logger = logging.getLogger(__name__)

_SENTIMENT_ANALYZER = None


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


def _build_conversation_text(ticket, comments):
    """Build a single text block of subject + chronological conversation for the AI."""
    lines = [f"Subject: {ticket.get('subject') or '(no subject)'}"]
    for c in comments:
        if not isinstance(c, dict):
            continue
        who = "Customer" if not c.get("from_support") else "Support"
        body = (c.get("body") or "").strip()
        if body:
            lines.append(f"{who}: {body}")
    return "\n\n".join(lines)


def _normalize_text_for_similarity(text):
    if not text:
        return ""
    text = str(text).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _ticket_customer_text(ticket):
    """Text used to find similar tickets (subject + customer messages)."""
    parts = [ticket.get("subject") or ""]
    for c in ticket.get("comments", []) or []:
        if not isinstance(c, dict):
            continue
        if c.get("from_support"):
            continue
        body = (c.get("body") or "").strip()
        if body:
            parts.append(body)
    return _normalize_text_for_similarity(" ".join(parts))


def _last_support_reply_text(ticket):
    """Return the most recent support reply text on a ticket, if any."""
    last = None
    for c in ticket.get("comments", []) or []:
        if not isinstance(c, dict):
            continue
        if not c.get("from_support"):
            continue
        body = (c.get("body") or "").strip()
        if body:
            last = body
    return last


def _similarity_score(a, b):
    """Cheap similarity score in [0,1] using both sequence and token overlap."""
    a = _normalize_text_for_similarity(a)
    b = _normalize_text_for_similarity(b)
    if not a or not b:
        return 0.0
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    a_set = set(a.split())
    b_set = set(b.split())
    if not a_set or not b_set:
        return float(seq)
    jacc = len(a_set & b_set) / max(1, len(a_set | b_set))
    # Weight sequence more; overlap helps for short texts
    return float(0.7 * seq + 0.3 * jacc)


def _find_similar_ticket_replies(current_ticket, limit=300, top_k=3, min_score=0.42):
    """
    Find similar past tickets that contain at least one support reply.
    Returns list of {ticket_id, score, reply}.
    """
    current_id = current_ticket.get("_id")
    query_text = _ticket_customer_text(current_ticket)
    if not query_text:
        return []

    # Only consider tickets that have support replies (so we can reuse real responses).
    cursor = (
        tickets.find({"comments.from_support": True})
        .sort("updated_at", -1)
        .limit(int(limit))
    )

    scored = []
    for t in cursor:
        if not isinstance(t, dict):
            continue
        if t.get("_id") == current_id:
            continue
        reply = _last_support_reply_text(t)
        if not reply:
            continue
        score = _similarity_score(query_text, _ticket_customer_text(t))
        if score >= float(min_score):
            scored.append({"ticket_id": t.get("_id"), "score": score, "reply": reply})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: int(top_k)]


def _get_sentiment_analyzer():
    """
    Optional: load Phase 2 analyzer if dependencies + model files exist.
    Keeps phase_2 folder unchanged; if unavailable, returns None.
    """
    global _SENTIMENT_ANALYZER
    if _SENTIMENT_ANALYZER is not None:
        return _SENTIMENT_ANALYZER
    try:
        from phase_2.sentiment_analysis import TicketSentimentAnalyzer  # type: ignore
    except Exception:
        _SENTIMENT_ANALYZER = None
        return None

    # By default, Phase 2 training writes these files into the directory it was run from
    # (often `phase_2/`). We support both locations without changing phase_2 code.
    prefix = os.getenv("TICKET_MODEL_PREFIX", "ticket_model")
    candidate_prefixes = [
        prefix,  # e.g. "ticket_model" in project root
        os.path.join("phase_2", prefix),  # e.g. "phase_2/ticket_model"
    ]

    # These are the filenames produced by TicketSentimentAnalyzer.save_models(prefix)
    def _required_files(pref):
        return [
            f"{pref}_issue.json",
            f"{pref}_priority.json",
            f"{pref}_sentiment.json",
            f"{pref}_vectorizer.pkl",
            f"{pref}_encoders.pkl",
        ]

    model_prefix_to_use = None
    for pref in candidate_prefixes:
        if all(os.path.exists(p) for p in _required_files(pref)):
            model_prefix_to_use = pref
            break
    if not model_prefix_to_use:
        _SENTIMENT_ANALYZER = None
        return None

    try:
        analyzer = TicketSentimentAnalyzer()
        analyzer.load_models(model_prefix_to_use)
        _SENTIMENT_ANALYZER = analyzer
        return _SENTIMENT_ANALYZER
    except Exception:
        logger.exception("Failed to load sentiment analyzer models")
        _SENTIMENT_ANALYZER = None
        return None


def _ai_suggest_reply(conversation_text, issue=None, priority=None):
    """Call Google Gemini to suggest a support reply. Returns (suggested_text, error_message)."""
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        return None, "GEMINI_API_KEY is not set in .env"
    context = f"Issue type: {issue or 'Not specified'}. Priority: {priority or 'Not specified'}."
    prompt = f"""You are a professional support agent. Below is a ticket conversation.

{context}

Conversation:
---
{conversation_text}
---

Write a complete, professional reply to the customer. The reply must include:
1. A brief opening that acknowledges the customer (e.g. thank them for reaching out or for an update).
2. A clear body that addresses their question or issue, with helpful next steps or information.
3. A polite closing (e.g. "Thank you for contacting us. Please let us know if you need anything else." or similar).

Be warm and professional. Keep the reply concise but complete—every sentence must be finished; never end mid-sentence or mid-thought. Do not invent specific details (prices, dates, account info, links). If the issue needs escalation or more info, say so clearly. Output only the reply body, no prefix or labels."""

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt, generation_config=genai.types.GenerationConfig(max_output_tokens=1024))
        if response.text:
            return response.text.strip(), None
        return None, "No reply generated from API"
    except Exception as e:
        logger.exception("Gemini suggest-reply failed")
        err_msg = str(e).strip() or type(e).__name__
        if "api_key" in err_msg.lower() or "auth" in err_msg.lower() or "invalid" in err_msg.lower() or "403" in err_msg:
            return None, "Invalid or expired Gemini API key. Check GEMINI_API_KEY in .env."
        if "not found" in err_msg.lower() or "404" in err_msg:
            return None, "Gemini model not available for this API key/project. Try a different model name."
        if "rate" in err_msg.lower() or "quota" in err_msg.lower() or "429" in err_msg:
            return None, "Gemini rate limit or quota exceeded. Try again later."
        return None, f"Gemini error: {err_msg[:200]}"


def _template_suggest_reply(ticket, customer_info=None):
    """
    Final fallback: lightweight canned replies by issue type.
    This ensures the agent always gets *something* even if Gemini is down/quota-limited.
    """
    ticket_id = ticket.get("_id") or ""
    subject = ticket.get("subject") or ""
    issue = (ticket.get("issue") or "General Inquiry").strip()
    company = (customer_info or {}).get("company_name") or "there"

    templates = {
        "Technical": (
            f"Hi {company},\n\n"
            "Thanks for reaching out. Sorry you’re running into this issue.\n\n"
            "Could you please share:\n"
            "- The steps you took right before it happened\n"
            "- Any error message you see (copy/paste or screenshot)\n"
            "- Your app/browser version and device/OS\n\n"
            f"We’re reviewing this under ticket {ticket_id} and will update you as soon as we have more.\n\n"
            "Thank you,\nSupport Team"
        ),
        "Billing": (
            f"Hi {company},\n\n"
            "Thanks for the details. We can help with this billing question.\n\n"
            "To investigate, please confirm:\n"
            "- The invoice/transaction ID (if available)\n"
            "- The date/time of the charge\n"
            "- The email on the billing account\n\n"
            f"We’ll review and follow up under ticket {ticket_id}.\n\n"
            "Thank you,\nSupport Team"
        ),
        "Account": (
            f"Hi {company},\n\n"
            "Thanks for contacting support. We can help with your account request.\n\n"
            "To proceed, please confirm:\n"
            "- The email on the account\n"
            "- Any recent changes you made (password/reset/2FA/device)\n\n"
            f"We’ll continue from ticket {ticket_id}.\n\n"
            "Thank you,\nSupport Team"
        ),
        "Fraud": (
            f"Hi {company},\n\n"
            "Thanks for flagging this. We take security concerns seriously.\n\n"
            "Please forward the suspicious message (including headers if possible) and let us know if any account changes occurred.\n\n"
            f"We’ll investigate and update you under ticket {ticket_id}.\n\n"
            "Thank you,\nSupport Team"
        ),
        "General Inquiry": (
            f"Hi {company},\n\n"
            "Thanks for reaching out. Happy to help.\n\n"
            f"Could you share a bit more detail about \"{subject}\" so we can point you to the right steps?\n\n"
            f"We’ll follow up under ticket {ticket_id}.\n\n"
            "Thank you,\nSupport Team"
        ),
    }

    return templates.get(issue, templates["General Inquiry"])


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


@app.route("/tickets/<ticket_id>/suggest-reply", methods=["GET"])
@login_required
def ticket_suggest_reply(ticket_id):
    """Return a suggested reply body using similar past replies, with Gemini as fallback."""
    ticket = tickets.find_one({"_id": ticket_id})
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    def _comment_time(c):
        if not isinstance(c, dict):
            return 0.0
        t = c.get("created_at")
        if t is None:
            return 0.0
        if hasattr(t, "timestamp"):
            return t.timestamp()
        return 0.0
    comments = sorted(
        [c for c in ticket.get("comments", []) if isinstance(c, dict)],
        key=_comment_time,
    )
    conversation_text = _build_conversation_text(ticket, comments)

    # 1) Try to reuse a real support reply from a similar past ticket (DB learning)
    similar = _find_similar_ticket_replies(ticket)
    if similar:
        best = similar[0]
        return jsonify(
            {
                "suggested_reply": best["reply"],
                "source": "similar_ticket",
                "similar_ticket_id": best.get("ticket_id"),
                "similarity_score": best.get("score"),
                "alternatives": similar[1:],
            }
        )

    # 2) Fallback to Gemini. Optionally enrich context using Phase 2 sentiment analyzer.
    issue = ticket.get("issue")
    priority = ticket.get("priority")
    analyzer = _get_sentiment_analyzer()
    if analyzer:
        try:
            pred = analyzer.predict(conversation_text)
            issue = pred.get("issue_type") or issue
            priority = pred.get("priority") or priority
            # Add sentiment into the conversation text so the model can adjust tone.
            sent = pred.get("sentiment")
            if sent:
                conversation_text = f"Customer sentiment: {sent}\n\n{conversation_text}"
        except Exception:
            logger.exception("Sentiment analyzer predict() failed; continuing without it")

    suggested, err_msg = _ai_suggest_reply(conversation_text, issue=issue, priority=priority)
    if suggested is None:
        # 3) Final fallback: templates (always available)
        customer_info = get_customer_info_for_ticket(ticket)
        templ = _template_suggest_reply(ticket, customer_info=customer_info)
        return jsonify(
            {
                "suggested_reply": templ,
                "source": "template",
                "error": err_msg or "Gemini unavailable; using template fallback.",
            }
        )
    return jsonify({"suggested_reply": suggested, "source": "gemini"})


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
