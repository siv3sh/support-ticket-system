import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from database import tickets  # noqa: E402


SEED_CASES = [
    {
        "subject": "Cannot login to my account",
        "issue": "Account",
        "priority": "High",
        "customer_body": "Hi Support, I can't log in even after resetting my password. Can you help?",
        "support_reply": (
            "Hi there,\n\n"
            "Thanks for reaching out—sorry you're having trouble logging in.\n\n"
            "Please confirm the email on the account and let us know:\n"
            "- What error message you see (if any)\n"
            "- Whether 2FA is enabled\n"
            "- When you last successfully logged in\n\n"
            "In the meantime, please try clearing your browser cache or using an incognito window.\n\n"
            "Thank you,\nSupport Team"
        ),
    },
    {
        "subject": "Payment failed and charged twice",
        "issue": "Billing",
        "priority": "High",
        "customer_body": "My payment failed but I see two charges pending. Please check.",
        "support_reply": (
            "Hi there,\n\n"
            "Thanks for reporting this—happy to help.\n\n"
            "Please share the transaction/invoice ID (if available) and the date/time of the charge. "
            "If the charges are still pending, they often drop automatically, but we’ll confirm on our side.\n\n"
            "Thank you,\nSupport Team"
        ),
    },
    {
        "subject": "₹₹₹",
        "issue": "Technical",
        "priority": "Medium",
        "customer_body": "The app crashes every time I open settings. I'm on Windows 11.",
        "support_reply": (
            "Hi there,\n\n"
            "Thanks for the details—sorry about the crash.\n\n"
            "Could you please share:\n"
            "- App version\n"
            "- Any error message or crash log (if available)\n"
            "- Whether this happens after a recent update\n\n"
            "As a quick step, reinstalling the app often resolves settings-related crashes. "
            "If it continues, we’ll escalate to engineering with the details.\n\n"
            "Thank you,\nSupport Team"
        ),
    },
    {
        "subject": "Requesting a refund",
        "issue": "Billing",
        "priority": "Medium",
        "customer_body": "I requested a refund 5 days ago. What's the status?",
        "support_reply": (
            "Hi there,\n\n"
            "Thanks for following up. We can check the refund status for you.\n\n"
            "Please confirm the email on the billing account and the invoice/transaction ID. "
            "Once confirmed, we’ll update you with the current status and expected timeline.\n\n"
            "Thank you,\nSupport Team"
        ),
    },
    {
        "subject": "How do I upgrade my subscription?",
        "issue": "Account",
        "priority": "Low",
        "customer_body": "How can I upgrade to the Enterprise plan?",
        "support_reply": (
            "Hi there,\n\n"
            "Thanks for reaching out—happy to help with an upgrade.\n\n"
            "You can upgrade from your Billing/Plan settings. If you don’t see the option, "
            "please share your account email and we’ll guide you through the correct steps.\n\n"
            "Thank you,\nSupport Team"
        ),
    },
]


def _new_ticket_id(i):
    ts = int(datetime.now(timezone.utc).timestamp())
    return f"SEED-{ts}-{i:03d}"


def seed(n=50):
    """
    Insert seed tickets with one customer message and one support reply.
    This gives the system an initial pool for "similar ticket reply" suggestions.
    """
    now = datetime.now(timezone.utc)
    inserted = 0
    for i in range(int(n)):
        c = SEED_CASES[i % len(SEED_CASES)]
        ticket_id = _new_ticket_id(i)
        ticket_doc = {
            "_id": ticket_id,
            "thread_id": None,
            "company_id": "seed_company",
            "customer_id": "seed_contact",
            "subject": c["subject"],
            "issue": c["issue"],
            "status": "Open",
            "priority": c["priority"],
            "created_at": now,
            "updated_at": now,
            "comments": [
                {
                    "comment_id": f"{ticket_id}-cust",
                    "message_id": f"{ticket_id}-cust",
                    "body": c["customer_body"],
                    "created_at": now,
                    "from_support": False,
                },
                {
                    "comment_id": f"{ticket_id}-sup",
                    "message_id": f"{ticket_id}-sup",
                    "body": c["support_reply"],
                    "created_at": now,
                    "from_support": True,
                },
            ],
        }
        tickets.insert_one(ticket_doc)
        inserted += 1
    return inserted


if __name__ == "__main__":
    # Default: seed 50 starter tickets
    count = int(os.getenv("SEED_TICKET_COUNT", "50"))
    inserted = seed(count)
    print(f"Inserted {inserted} seed tickets.")

