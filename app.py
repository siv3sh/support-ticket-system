"""
Flask app for support ticket dashboard.
View tickets, full conversation (customer + support), reply, manual create.
Customer identification & domain tagging, basic role-based access.
"""
import difflib
import logging
import os
import re
import secrets
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

import google.generativeai as genai

from bson.objectid import ObjectId
from database import tickets, customers, agents, admins, users, audit_logs
from email_service import (
    get_next_ticket_number,
    normalize_msg_id,
    send_password_reset_email as _send_password_reset_email,
    send_support_reply,
    send_user_created_email,
)
from services.report_services import (
    get_total_tickets_status,
    get_average_resolution_time,
    get_sla_metrics,
    get_ticket_trend,
    get_issue_distribution,
    get_tickets_by_customer,
    get_agent_performance,
)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-production")
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=int(os.getenv("SESSION_LIFETIME_MINUTES", "60")))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.getenv("FLASK_ENV") == "production":
    app.config["SESSION_COOKIE_SECURE"] = True

CSRFProtect(app)
limiter = Limiter(key_func=get_remote_address, app=app, default_limits=["200 per day"])

logger = logging.getLogger(__name__)

_SENTIMENT_ANALYZER = None


def _audit_log(action, details=None, ticket_id=None, user_id=None):
    """Record an audit event."""
    try:
        audit_logs.insert_one({
            "action": action,
            "details": details or {},
            "ticket_id": ticket_id,
            "user_id": user_id or session.get("user_id"),
            "user_email": session.get("user_email"),
            "ip": get_remote_address(),
            "created_at": datetime.now(timezone.utc),
        })
    except Exception:
        logger.exception("Audit log insert failed")


def login_required(f):
    @wraps(f)
    def inner(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in.", "error")
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return inner


def role_required(*allowed_roles):
    """Decorator: require one of allowed_roles (e.g. 'admin', 'agent')."""
    def decorator(f):
        @wraps(f)
        def inner(*args, **kwargs):
            if not session.get("user_id"):
                flash("Please log in.", "error")
                return redirect(url_for("login", next=request.url))
            role = (session.get("role") or "").lower()
            if role not in [r.lower() for r in allowed_roles]:
                flash("You do not have permission for this action.", "error")
                return redirect(url_for("ticket_list"))
            return f(*args, **kwargs)
        return inner
    return decorator


def get_customer_ticket_pairs(user_email):
    """For role customer: (company_id, customer_id) pairs where contact email matches user_email."""
    if not user_email:
        return set()
    pairs = set()
    for company in customers.find({}):
        if not isinstance(company, dict):
            continue
        cid = company.get("_id")
        for contact in company.get("contacts", []) or []:
            if not isinstance(contact, dict):
                continue
            if (contact.get("email") or "").strip().lower() == user_email.strip().lower():
                pairs.add((cid, contact.get("customer_id")))
    return pairs


def customer_can_access_ticket(ticket, user_email):
    """True if ticket belongs to the customer (contact email = user_email)."""
    if not ticket or not user_email:
        return False
    pairs = get_customer_ticket_pairs(user_email)
    return (ticket.get("company_id"), ticket.get("customer_id")) in pairs


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
@limiter.limit("5 per minute")
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
    user = None
    role = None
    # 1) Check unified users collection first
    user = users.find_one({"email": email})
    if user:
        role = (user.get("role") or "agent").lower()
    # 2) Fallback: legacy admins / agents
    if not user:
        user = admins.find_one({"email": email})
        if user:
            role = "admin"
    if not user:
        user = agents.find_one({"email": email})
        if user:
            role = "agent"
    # 3) First-time setup: only when explicitly allowed (dev or ALLOW_FIRST_TIME_SETUP=1)
    allow_first_time = os.getenv("ALLOW_FIRST_TIME_SETUP", "").strip() == "1" or os.getenv("FLASK_ENV") == "development"
    if not user and allow_first_time and users.count_documents({}) == 0 and admins.count_documents({}) == 0 and agents.count_documents({}) == 0:
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
    _audit_log("login", {"email": email, "role": role})
    next_url = request.args.get("next") or url_for("ticket_list")
    return redirect(next_url)


@app.route("/logout")
def logout():
    _audit_log("logout", {})
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/health")
def health():
    """Health check for load balancers / monitoring."""
    try:
        tickets.database.client.admin.command("ping")
        return jsonify({"status": "ok", "database": "connected"}), 200
    except Exception as e:
        logger.exception("Health check failed")
        return jsonify({"status": "error", "database": str(e)}), 503


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
    # Customer role: only tickets where their email is the contact
    role = (session.get("role") or "").lower()
    if role == "customer":
        pairs = get_customer_ticket_pairs(session.get("user_email") or "")
        if not pairs:
            query["$or"] = [{"company_id": "__none__", "customer_id": "__none__"}]  # match nothing
        else:
            query["$or"] = [{"company_id": cid, "customer_id": cvid} for (cid, cvid) in pairs]
    # Pagination
    per_page = max(1, min(100, int(os.getenv("TICKETS_PER_PAGE", "25"))))
    page = max(1, int(request.args.get("page", "1")))
    total = tickets.count_documents(query)
    total_pages = (total + per_page - 1) // per_page if total else 1
    page = min(page, total_pages) if total_pages else 1
    skip = (page - 1) * per_page
    ticket_list = list(tickets.find(query).sort("updated_at", -1).skip(skip).limit(per_page))
    # Attach customer/domain info for each ticket (customer identification & domain tagging)
    for t in ticket_list:
        t["_customer_info"] = get_customer_info_for_ticket(t)
    # All domains for filter dropdown (only for admin/agent/viewer)
    domains = list(customers.distinct("domain")) if role != "customer" else []
    domains.sort()
    return render_template(
        "tickets_list.html",
        tickets=ticket_list,
        domains=domains,
        page=page,
        total_pages=total_pages,
        total=total,
        per_page=per_page,
    )


@app.route("/tickets/create", methods=["GET", "POST"])
@login_required
@role_required("admin", "agent")
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
    assigned_to = session.get("user_id") or session.get("user_email")
    tickets.insert_one({
        "_id": ticket_id,
        "thread_id": None,
        "company_id": company_id,
        "customer_id": customer_id,
        "subject": subject,
        "issue": issue,
        "status": "Open",
        "priority": priority,
        "assigned_to": assigned_to,
        "created_at": now,
        "updated_at": now,
        "comments": [initial_comment],
    })
    _audit_log("ticket_create", {"ticket_id": ticket_id, "subject": subject}, ticket_id=ticket_id)
    flash(f"Ticket {ticket_id} created.", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/tickets/<ticket_id>")
@login_required
def ticket_detail(ticket_id):
    ticket = tickets.find_one({"_id": ticket_id})
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("ticket_list"))
    # Customer can only view their own tickets
    role = (session.get("role") or "").lower()
    if role == "customer" and not customer_can_access_ticket(ticket, session.get("user_email")):
        flash("You do not have access to this ticket.", "error")
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
    can_reply = role in ("admin", "agent") or (role == "customer" and customer_can_access_ticket(ticket, session.get("user_email")))
    can_change_status = can_reply  # same as reply for simplicity
    can_suggest_reply = role in ("admin", "agent")
    return render_template(
        "ticket_detail.html",
        ticket=ticket,
        comments=comments,
        customer_info=customer_info,
        can_reply=can_reply,
        can_change_status=can_change_status,
        can_suggest_reply=can_suggest_reply,
    )


@app.route("/tickets/<ticket_id>/suggest-reply", methods=["GET"])
@login_required
@role_required("admin", "agent")
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
    role = (session.get("role") or "").lower()
    if role == "customer":
        if not customer_can_access_ticket(ticket, session.get("user_email")):
            flash("You do not have access to this ticket.", "error")
            return redirect(url_for("ticket_list"))
    elif role == "viewer":
        flash("Viewers cannot add replies.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Reply body is required.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    now = datetime.now(timezone.utc)

    # Customer: add comment only (no email). Admin/Agent: send email and add support comment.
    if role == "customer":
        comment = {
            "comment_id": f"cust-{ticket_id}-{now.timestamp():.0f}",
            "message_id": f"cust-{ticket_id}-{now.timestamp():.0f}",
            "body": body,
            "created_at": now,
            "from_support": False,
        }
        tickets.update_one(
            {"_id": ticket_id},
            {"$push": {"comments": comment}, "$set": {"updated_at": now}},
        )
        _audit_log("ticket_comment", {"from": "customer"}, ticket_id=ticket_id)
        flash("Comment added.", "success")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    to_email = get_customer_email_for_ticket(ticket)
    if not to_email:
        flash("Customer email not found for this ticket.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

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
    _audit_log("ticket_reply", {}, ticket_id=ticket_id)
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
    role = (session.get("role") or "").lower()
    if role == "viewer":
        flash("Viewers cannot change ticket status.", "error")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))
    if role == "customer" and not customer_can_access_ticket(ticket, session.get("user_email")):
        flash("You do not have access to this ticket.", "error")
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
    _audit_log("ticket_status", {"new_status": new_status}, ticket_id=ticket_id)
    flash(f"Ticket set to {new_status}.", "success")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


# ---------- User management (Admin only) ----------
ROLES = [("admin", "Admin (Full access)"), ("agent", "Agent (View tickets, add comments/updates)"), ("viewer", "Viewer (View only)"), ("customer", "Customer (Own tickets only)")]


def _get_user_by_uid(uid):
    """Get user from users collection by id (ObjectId or string)."""
    if not uid:
        return None
    try:
        return users.find_one({"_id": ObjectId(uid)})
    except Exception:
        return users.find_one({"_id": uid})


@app.route("/reports")
@login_required
@role_required("admin")
def report_dashboard():
    status_data = get_total_tickets_status()
    avg_resolution = get_average_resolution_time()
    sla = get_sla_metrics()
    trend = get_ticket_trend(30)
    issue_data = get_issue_distribution()
    customer_data = get_tickets_by_customer()
    agent_perf = get_agent_performance()
    return render_template(
        "report_dashboard.html",
        status_data=status_data,
        avg_resolution=avg_resolution,
        sla=sla,
        trend=trend,
        issue_data=issue_data,
        customer_data=customer_data,
        agent_perf=agent_perf,
    )


@app.route("/users")
@login_required
@role_required("admin")
def user_list():
    user_list = list(users.find({}).sort("created_at", -1))
    return render_template("user_list.html", user_list=user_list)


@app.route("/users/create", methods=["GET", "POST"])
@login_required
@role_required("admin")
def user_create():
    if request.method == "GET":
        return render_template("user_create.html", roles=ROLES)
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    phone = (request.form.get("phone") or "").strip()
    address = (request.form.get("address") or "").strip()
    designation = (request.form.get("designation") or "").strip()
    role = (request.form.get("role") or "agent").strip().lower()
    password = request.form.get("password") or ""
    send_temp = request.form.get("send_temp_password") == "1"
    if not name:
        flash("Name is required.", "error")
        return redirect(url_for("user_create"))
    if not email:
        flash("Email is required.", "error")
        return redirect(url_for("user_create"))
    if role not in ("admin", "agent", "viewer", "customer"):
        flash("Invalid role.", "error")
        return redirect(url_for("user_create"))
    if users.find_one({"email": email}):
        flash("A user with this email already exists.", "error")
        return redirect(url_for("user_create"))
    if send_temp:
        temp_password = secrets.token_urlsafe(12)
        password_hash = generate_password_hash(temp_password)
    else:
        if not password:
            flash("Password is required when not sending a temporary password.", "error")
            return redirect(url_for("user_create"))
        temp_password = None
        password_hash = generate_password_hash(password)
    now = datetime.now(timezone.utc)
    doc = {
        "name": name,
        "email": email,
        "phone": phone,
        "address": address,
        "designation": designation,
        "role": role,
        "password_hash": password_hash,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    users.insert_one(doc)
    _audit_log("user_create", {"email": email, "role": role})
    if send_temp and temp_password:
        login_url = request.url_root.rstrip("/") + url_for("login")
        send_user_created_email(email, name, temp_password, login_url)
        flash(f"User {email} created. A temporary password was sent by email.", "success")
    else:
        flash(f"User {email} created.", "success")
    return redirect(url_for("user_list"))


@app.route("/users/<uid>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def user_edit(uid):
    user = _get_user_by_uid(uid)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("user_list"))
    if request.method == "GET":
        return render_template("user_edit.html", user=user, roles=ROLES)
    name = (request.form.get("name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    address = (request.form.get("address") or "").strip()
    designation = (request.form.get("designation") or "").strip()
    role = (request.form.get("role") or "agent").strip().lower()
    if not name:
        flash("Name is required.", "error")
        return redirect(url_for("user_edit", uid=uid))
    if role not in ("admin", "agent", "viewer", "customer"):
        flash("Invalid role.", "error")
        return redirect(url_for("user_edit", uid=uid))
    now = datetime.now(timezone.utc)
    users.update_one(
        {"_id": user["_id"]},
        {"$set": {"name": name, "phone": phone, "address": address, "designation": designation, "role": role, "updated_at": now}}
    )
    _audit_log("user_edit", {"email": user.get("email"), "role": role})
    flash("User updated.", "success")
    return redirect(url_for("user_list"))


@app.route("/users/<uid>/deactivate", methods=["POST"])
@login_required
@role_required("admin")
def user_deactivate(uid):
    user = _get_user_by_uid(uid)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("user_list"))
    if str(user["_id"]) == session.get("user_id"):
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("user_list"))
    users.update_one(
        {"_id": user["_id"]},
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}}
    )
    _audit_log("user_deactivate", {"email": user.get("email")})
    flash("User deactivated. They can no longer log in.", "success")
    return redirect(url_for("user_list"))


@app.route("/users/<uid>/activate", methods=["POST"])
@login_required
@role_required("admin")
def user_activate(uid):
    user = _get_user_by_uid(uid)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("user_list"))
    users.update_one(
        {"_id": user["_id"]},
        {"$set": {"is_active": True, "updated_at": datetime.now(timezone.utc)}}
    )
    _audit_log("user_activate", {"email": user.get("email")})
    flash("User activated.", "success")
    return redirect(url_for("user_list"))


def _get_current_user():
    """Get current user doc from session (users collection or legacy admins/agents)."""
    uid = session.get("user_id")
    email = session.get("user_email")
    if not uid or not email:
        return None
    user = users.find_one({"email": email})
    if user:
        return user
    user = admins.find_one({"email": email})
    if user:
        return user
    user = agents.find_one({"email": email})
    if user:
        return user
    # First-time setup or unknown: minimal doc from session so profile page can render
    return {"_id": uid, "email": email, "name": session.get("user_name"), "phone": "", "address": "", "designation": "", "password_hash": None}


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = _get_current_user()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("ticket_list"))
    # Legacy admins/agents don't have profile in users; they can only change password if we add it to legacy
    in_users = users.find_one({"email": user.get("email")}) is not None
    if request.method == "GET":
        return render_template("profile.html", user=user, in_users=in_users)
    if not in_users:
        flash("Profile update is only available for users in the new system.", "error")
        return redirect(url_for("ticket_list"))
    name = (request.form.get("name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    address = (request.form.get("address") or "").strip()
    designation = (request.form.get("designation") or "").strip()
    if not name:
        flash("Name is required.", "error")
        return redirect(url_for("profile"))
    users.update_one(
        {"email": user["email"]},
        {"$set": {"name": name, "phone": phone, "address": address, "designation": designation, "updated_at": datetime.now(timezone.utc)}}
    )
    session["user_name"] = name
    flash("Profile updated.", "success")
    return redirect(url_for("profile"))


@app.route("/profile/change-password", methods=["GET", "POST"])
@login_required
def profile_change_password():
    user = _get_current_user()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("ticket_list"))
    in_users = users.find_one({"email": user.get("email")}) is not None
    if not in_users:
        # Legacy: allow password change if they have password_hash (admins/agents)
        if not user.get("password_hash"):
            flash("Password change is not available for this account.", "error")
            return redirect(url_for("ticket_list"))
    if request.method == "GET":
        return render_template("profile_change_password.html")
    current = request.form.get("current_password") or ""
    new_pass = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not current:
        flash("Current password is required.", "error")
        return redirect(url_for("profile_change_password"))
    ph = user.get("password_hash")
    if ph and not check_password_hash(ph, current):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("profile_change_password"))
    if not new_pass or len(new_pass) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect(url_for("profile_change_password"))
    if new_pass != confirm:
        flash("New password and confirmation do not match.", "error")
        return redirect(url_for("profile_change_password"))
    password_hash = generate_password_hash(new_pass)
    if in_users:
        users.update_one(
            {"email": user["email"]},
            {"$set": {"password_hash": password_hash, "updated_at": datetime.now(timezone.utc)}}
        )
    else:
        # Legacy: update admins or agents
        if admins.find_one({"email": user["email"]}):
            admins.update_one({"email": user["email"]}, {"$set": {"password_hash": password_hash}})
        else:
            agents.update_one({"email": user["email"]}, {"$set": {"password_hash": password_hash}})
    flash("Password updated. Please log in again with your new password.", "success")
    return redirect(url_for("logout"))


# ---------- Forgot password ----------
RESET_TOKEN_EXPIRE_HOURS = 1




@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("3 per minute")
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")
    email = (request.form.get("email") or "").strip().lower()
    if not email:
        flash("Email is required.", "error")
        return redirect(url_for("forgot_password"))
    user = users.find_one({"email": email})
    if not user:
        # Don't reveal whether email exists
        flash("If an account exists for this email, you will receive a reset link.", "success")
        return redirect(url_for("login"))
    if not user.get("is_active", True):
        flash("If an account exists for this email, you will receive a reset link.", "success")
        return redirect(url_for("login"))
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=RESET_TOKEN_EXPIRE_HOURS)
    users.update_one(
        {"email": email},
        {"$set": {"password_reset_token": token, "password_reset_expires": expires}}
    )
    reset_link = request.url_root.rstrip("/") + url_for("reset_password", token=token)
    if _send_password_reset_email(email, reset_link):
        flash("If an account exists for this email, you will receive a reset link.", "success")
    else:
        flash("Failed to send reset email. Try again later.", "error")
    return redirect(url_for("login"))


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = users.find_one({"password_reset_token": token})
    if not user or not user.get("password_reset_expires"):
        flash("Invalid or expired reset link.", "error")
        return redirect(url_for("login"))
    if datetime.now(timezone.utc) > user["password_reset_expires"]:
        users.update_one(
            {"_id": user["_id"]},
            {"$unset": {"password_reset_token": "", "password_reset_expires": ""}}
        )
        flash("Reset link has expired. Request a new one.", "error")
        return redirect(url_for("login"))
    if request.method == "GET":
        return render_template("reset_password.html", token=token)
    new_pass = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not new_pass or len(new_pass) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("reset_password", token=token))
    if new_pass != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("reset_password", token=token))
    users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {"password_hash": generate_password_hash(new_pass), "updated_at": datetime.now(timezone.utc)},
            "$unset": {"password_reset_token": "", "password_reset_expires": ""}
        }
    )
    _audit_log("password_reset", {"email": user.get("email")}, user_id=str(user["_id"]))
    flash("Password updated. You can log in now.", "success")
    return redirect(url_for("login"))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5005"))
    debug = os.getenv("FLASK_ENV") == "development" or os.getenv("DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
