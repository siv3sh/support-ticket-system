from database import tickets, customers, agents, admins, users, audit_logs

tickets.create_index("thread_id")
tickets.create_index("company_id")
tickets.create_index("customer_id")
tickets.create_index("agent_id")
tickets.create_index("comments.message_id")
tickets.create_index("assigned_to")

customers.create_index("domain")
agents.create_index("email")
admins.create_index("email")

users.create_index("email", unique=True)
users.create_index("role")
users.create_index("password_reset_token")

audit_logs.create_index("created_at")
audit_logs.create_index("user_id")
audit_logs.create_index("action")

print("Indexes created successfully")
