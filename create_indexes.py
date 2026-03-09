from database import tickets, customers, agents, admins

tickets.create_index("thread_id")
tickets.create_index("company_id")
tickets.create_index("customer_id")
tickets.create_index("agent_id")
tickets.create_index("comments.message_id")

customers.create_index("domain")
agents.create_index("email")
admins.create_index("email")

print("Indexes created successfully")
