"""
Fetch emails via IMAP, create/update tickets, send acknowledgements and support replies.
"""

import os
import time
import email
import imaplib
import smtplib
import logging
import re
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from dotenv import load_dotenv

from database import customers, tickets, agents, admins

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

CHECK_INTERVAL = int(os.getenv("EMAIL_POLL_INTERVAL", "10"))
AUTO_REPLY_ENABLED = os.getenv("AUTO_REPLY_ENABLED", "true").lower() in ("true", "1", "yes")

def normalize_msg_id(value):
    if not value:
        return None
    return value.strip().replace("<", "").replace(">", "")


def parse_message_ids(header_value):
    """Split References/In-Reply-To by whitespace or comma; return list of normalized ids."""
    if not header_value or not str(header_value).strip():
        return []
    parts = re.split(r"[\s,]+", str(header_value).strip())
    return [normalize_msg_id(p) for p in parts if p and normalize_msg_id(p)]

def get_next_sequence(prefix, collection, pad=3):

    last_doc = collection.find_one(
        {"_id": {"$regex": f"^{prefix}-A"}},
        sort=[("_id", -1)]
    )

    if not last_doc:
        return f"{prefix}-A{1:0{pad}d}"

    try:
        last_num = int(last_doc["_id"].split("A")[1])
        return f"{prefix}-A{last_num + 1:0{pad}d}"
    except:
        return f"{prefix}-A{1:0{pad}d}"

# email processing
def get_body(msg):
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                body = part.get_payload(decode=True).decode(errors="ignore")
                break
    else:
        body = msg.get_payload(decode=True).decode(errors="ignore")

    body = body.replace("\r\n", "\n")

    if "\nOn " in body and " wrote:" in body:
        body = body.split("\nOn ")[0]

    return body.strip()


def extract_attachments(msg):
    attachments = []

    for part in msg.walk():
        filename = part.get_filename()
        if filename:
            file_data = part.get_payload(decode=True)
            attachments.append({
                "filename": filename,
                "content_type": part.get_content_type(),
                "size_in_bytes": len(file_data)
            })

    return attachments


def extract_sender_email(from_header):
    if not from_header:
        return None

    s = from_header.strip()

    if "<" in s and ">" in s:
        return s.split("<")[1].split(">")[0].strip().lower()

    return s.lower()

def resolve_and_update_customer(sender_email):

    if not sender_email or "@" not in sender_email:
        logger.info("Invalid sender email - Skipping customer resolution")
        return None, None

    domain = sender_email.split("@")[1].lower()
    now = datetime.now(timezone.utc)

    customer = customers.find_one({"domain": domain})

    #  COMPANY DOES NOT EXIST
    if not customer:

        company_id = get_next_sequence("CMP", customers)
        contact_id = get_next_sequence("CUS", customers)

        new_customer = {
            "_id": company_id,
            "company_name": domain.split(".")[0],
            "domain": domain,
            "address": None,
            "created_at": now,
            "contacts": [
                {
                    "customer_id": contact_id,
                    "name": None,
                    "email": sender_email,
                    "contact": None,
                    "agent_id": None,
                    "created_at": now
                }
            ]
        }

        customers.insert_one(new_customer)

        logger.info(f" New company detected - {company_id} ({domain})")
        logger.info(f" New customer/contact created - {contact_id} ({sender_email})")

        return company_id, contact_id

    #  COMPANY EXISTS - CHECK CONTACTS
    for contact in customer.get("contacts", []):
        if contact.get("email") == sender_email:
            return customer["_id"], contact.get("customer_id")

    #  NEW CONTACT
    contact_id = get_next_sequence("CUS", customers)

    new_contact = {
        "customer_id": contact_id,
        "name": None,
        "email": sender_email,
        "contact": None,
        "agent_id": None,
        "created_at": now
    }

    customers.update_one(
        {"_id": customer["_id"]},
        {"$push": {"contacts": new_contact}}
    )

    logger.info(f" Existing company - {customer['_id']} ({domain})")
    logger.info(f" New contact added - {contact_id} ({sender_email})")

    return customer["_id"], contact_id

# fetch email
def fetch_emails():
    all_emails = []

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select("Inbox")

        status, message = mail.uid('search', None, 'UNSEEN')
        email_uids = message[0].split()

        if not email_uids:
            mail.logout()
            return []

        logger.info(f" Found {len(email_uids)} new email(s)")

        for uid in email_uids:
            status, data = mail.uid('fetch', uid, '(RFC822 X-GM-THRID)')
            raw_mail = data[0][1]
            msg = email.message_from_bytes(raw_mail)

            thrid = None
            response_info = data[0][0].decode()
            if "X-GM-THRID" in response_info:
                thrid = response_info.split("X-GM-THRID")[1].split()[0]

            email_data = {
                "subject": msg.get("Subject"),
                "from": msg.get("From"),
                "to": msg.get("To"),
                "message_id": msg.get("Message-ID"),
                "in_reply_to": msg.get("In-Reply-To"),
                "references": msg.get("References"),
                "body": get_body(msg),
                "thrid": thrid,
                "uid": uid.decode(),
                "attachments": extract_attachments(msg)
            }

            all_emails.append(email_data)

        mail.logout()
        return all_emails

    except Exception as e:
        logger.error(f"Error fetching emails: {e}")
        return []


def mark_emails_as_seen(processed_uids):
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select("Inbox")

        for uid in processed_uids:
            mail.uid('store', uid.encode(), '+FLAGS', '\\Seen')

        mail.logout()

    except Exception as e:
        logger.error(f"Error marking emails seen: {e}")

def detect_issue(subject, body):

    text = f"{subject or ''} {body or ''}".lower()

    rules = {
        "Login Problem": ["login", "sign in", "cannot access", "password"],
        "Payment Issue": ["payment", "charged", "billing", "refund", "transaction"],
        "Account Issue": ["account", "blocked", "disabled", "suspended"],
        "Bug Report": ["bug", "error", "issue", "not working", "failure"],
        "Feature Request": ["feature", "request", "add", "enhancement"]
    }

    for issue_name, keywords in rules.items():
        for word in keywords:
            if word in text:
                return issue_name

    return "General Inquiry"

# ticket logic
def get_next_ticket_number():

    last_ticket = tickets.find_one(sort=[("_id", -1)])

    if not last_ticket:
        return "TCK-0001"

    try:
        last_num = int(last_ticket["_id"].split("-")[1])
        next_num = last_num + 1
        return f"TCK-{next_num:04d}"
    except:
        # fallback safety (rare edge case)
        return f"TCK-{int(time.time())}"

def resolve_or_create_ticket(email_data):

    in_reply_to_ids = parse_message_ids(email_data.get("in_reply_to"))
    ref_ids = parse_message_ids(email_data.get("references"))
    all_ref_ids = list(dict.fromkeys(in_reply_to_ids + ref_ids))
    thrid = email_data.get("thrid")

    # 1. In-Reply-To / References: match by comment or ack_message_id so reply-after-ack finds ticket
    for ref in all_ref_ids:
        existing = tickets.find_one({"comments.message_id": ref})
        if existing:
            logger.info(f"Matched via In-Reply-To/References (comment) → {existing['_id']}")
            return existing["_id"], False
        existing = tickets.find_one({"ack_message_id": ref})
        if existing:
            logger.info(f"Matched via reply to ack → {existing['_id']}")
            return existing["_id"], False

    # 2. Gmail Thread ID
    if thrid:
        existing = tickets.find_one({"thread_id": thrid})
        if existing:
            logger.info(f"Matched via Thread ID → {existing['_id']}")
            return existing["_id"], False

    # 3. Subject line: "Re: Support Request [TCK-0001]" — some clients don't set In-Reply-To
    subject = (email_data.get("subject") or "") or ""
    match = re.search(r"\[?(TCK-\d+)\]?", subject, re.I)
    if match:
        ticket_id_from_subject = match.group(1)
        existing = tickets.find_one({"_id": ticket_id_from_subject})
        if existing:
            logger.info(f"Matched via subject ticket ID → {existing['_id']}")
            return existing["_id"], False

    # 4. New Ticket
    new_ticket_id = get_next_ticket_number()
    logger.info(f"Creating NEW ticket → {new_ticket_id}")

    return new_ticket_id, True

def persist_ticket_and_message(ticket_id, is_new, email_data, company_id, customer_id):

    now = datetime.now(timezone.utc)

    message_id = normalize_msg_id(email_data.get("message_id"))

    issue = detect_issue(email_data.get("subject"), email_data.get("body"))

    # PRIORITY RULE
    priority = "High" if issue == "Payment Issue" else "Medium"

    comment = {
        "comment_id": message_id,
        "message_id": message_id,
        "body": email_data.get("body"),
        "created_at": now,
        "from_support": False
    }

    if is_new:

        tickets.insert_one({
            "_id": ticket_id,
            "thread_id": email_data.get("thrid"),
            "company_id": company_id,
            "customer_id": customer_id,
            "subject": email_data.get("subject"),
            "issue": issue,
            "status": "Open",
            "priority": priority,
            "created_at": now,
            "updated_at": now,
            "comments": [comment]
        })

        logger.info(f"Ticket CREATED → {ticket_id} | Issue: {issue} | Priority: {priority}")

    else:

        duplicate = tickets.find_one({
            "_id": ticket_id,
            "comments.message_id": message_id
        })

        if duplicate:
            logger.info("Duplicate reply → Ignored")
            return

        tickets.update_one(
            {"_id": ticket_id},
            {
                "$push": {"comments": comment},
                "$set": {"updated_at": now}
            }
        )

        logger.info(f"Ticket UPDATED → {ticket_id}")


# notification — send ack and return (message_id, body) so caller can store in DB
def send_ticket_ack(ticket_id, to_email):

    if not AUTO_REPLY_ENABLED or not to_email:
        return None, None

    subject = f"Re: Support Request [{ticket_id}]"

    body = f"""Ticket {ticket_id} has been created.

Our team will review it and get back to you as soon as possible.

Please reply to this email to add more information to the same ticket. Do not change the subject line.
"""

    # Create Message-ID first so we can save it — when customer replies, we match by this
    ack_message_id = make_msgid()

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Support", EMAIL_ADDRESS))
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = ack_message_id

    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, [to_email], msg.as_string())

        logger.info(f"Ack sent - {to_email}")
        return ack_message_id, body

    except Exception as e:
        logger.error(f"Ack failed: {e}")
        return None, None


# Send a support reply email; returns (message_id, body) on success so caller can store in DB
def send_support_reply(ticket_id, to_email, body, subject=None, in_reply_to=None, references=None):
    if not to_email or not body:
        return None, None
    subject = subject or f"Re: Support Request [{ticket_id}]"
    reply_message_id = make_msgid()
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Support", EMAIL_ADDRESS))
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = reply_message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to if in_reply_to.startswith("<") else f"<{in_reply_to}>"
    if references:
        ref_str = " ".join(r if r.startswith("<") else f"<{r}>" for r in references)
        msg["References"] = ref_str
    msg.attach(MIMEText(body.strip(), "plain"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, [to_email], msg.as_string())
        logger.info(f"Support reply sent - {to_email}")
        return reply_message_id, body.strip()
    except Exception as e:
        logger.error(f"Support reply failed: {e}")
        return None, None


def send_user_created_email(to_email, name, temp_password, login_url):
    """Send email to new user with temporary password. Returns True on success."""
    if not to_email or not temp_password:
        return False
    subject = "Your support portal account"
    body = f"""Hi {name or 'there'},

Your account has been created.

Email: {to_email}
Temporary password: {temp_password}

Log in here: {login_url}

Please change your password after your first login (Profile → Change password).

— Support Team
"""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Support", EMAIL_ADDRESS))
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText(body.strip(), "plain"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, [to_email], msg.as_string())
        logger.info(f"User created email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"User created email failed: {e}")
        return False


def send_password_reset_email(to_email, reset_link):
    """Send password reset link. Returns True on success."""
    if not to_email or not reset_link:
        return False
    subject = "Reset your support portal password"
    body = f"""Hi,

You requested a password reset.

Click the link below to set a new password (valid for 1 hour):

{reset_link}

If you didn't request this, you can ignore this email.

— Support Team
"""
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Support", EMAIL_ADDRESS))
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid()
    msg.attach(MIMEText(body.strip(), "plain"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, [to_email], msg.as_string())
        logger.info(f"Password reset email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Password reset email failed: {e}")
        return False


# pipeline
def process_one_email(email_data):

    if not email_data.get("message_id"):
        email_data["message_id"] = f"reply-{email_data.get('uid', '')}-{time.time():.0f}"
        logger.warning(f"No Message-ID — using fallback so reply is still saved")

    sender = extract_sender_email(email_data.get("from"))

    company_id, customer_id = resolve_and_update_customer(sender)

    ticket_id, is_new = resolve_or_create_ticket(email_data)

    persist_ticket_and_message(ticket_id, is_new, email_data, company_id, customer_id)

    if is_new:
        ack_message_id, ack_body = send_ticket_ack(ticket_id, sender)
        if ack_message_id:
            mid = normalize_msg_id(ack_message_id)
            now = datetime.now(timezone.utc)
            support_comment = {
                "comment_id": mid,
                "message_id": mid,
                "body": ack_body or "",
                "created_at": now,
                "from_support": True
            }
            tickets.update_one(
                {"_id": ticket_id},
                {
                    "$set": {"ack_message_id": mid, "updated_at": now},
                    "$push": {"comments": support_comment}
                }
            )

    return True

def run_pipeline():

    emails = fetch_emails()

    if not emails:
        logger.info("No new emails")
        return

    processed_uids = []

    for email_data in emails:
        success = process_one_email(email_data)
        if success:
            processed_uids.append(email_data["uid"])

    if processed_uids:
        mark_emails_as_seen(processed_uids)

# main
if __name__ == "__main__":

    logger.info(" Email - Ticket System Started")

    while True:
        try:
            run_pipeline()
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            logger.info(" ... ")
            break
