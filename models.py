from datetime import datetime, UTC

def customer_model():
    return {
        "_id": None,
        "company_name": None,
        "domain": None,
        "address": None,
        "created_at": datetime.now(UTC),
        "contacts": []
    }

def contact_model():
    return {
        "customer_id": None,
        "name": None,
        "email": None,
        "contact": None,
        "agent_id": None,
        "created_at": datetime.now(UTC)
    }

def ticket_model():
    return {
        "_id": None,
        "thread_id": None,
        "company_id": None,
        "customer_id": None,
        "subject": None,
        "issue": None,
        "from": None,
        "to": None,
        "status": "Open",
        "priority": "Medium",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "comments": []
    }

def comment_model():
    return {
        "comment_id": None,
        "message_id": None,
        "in_reply_to": None,
        "references": None,
        "body": None,
        "attachments": [],
        "agent_id": None,
        "created_at": datetime.now(UTC)
    }

def agent_model():
    return {
        "_id": None,
        "name": None,
        "email": None,
        "contact": None,
        "department": None,
        "timeslot": None,
        "password_hash": None,
        "permissions": [],
        "is_active": True,
        "created_at": datetime.now(UTC)
    }

def admin_model():
    return {
        "_id": None,
        "name": None,
        "email": None,
        "contact": None,
        "password_hash": None,
        "permissions": [],
        "is_active": True,
        "created_at": datetime.now(UTC)
    }