# Support Ticket System

A simple support ticket system: emails become tickets, and you can view and reply from a web dashboard.

## What’s used

- **Python 3** – app and scripts
- **Flask** – web dashboard (login, ticket list, reply)
- **MongoDB** – tickets, customers, agents, admins
- **Gmail (IMAP/SMTP)** – fetch new emails and send replies

## Setup

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **MongoDB**  
   Edit `database.py` (and the same settings in `email_fetching.py`) with your MongoDB host, port, username, and password.

3. **Environment variables**  
   Create a `.env` file in the project root:

   ```
   EMAIL_ADDRESS=your-support@gmail.com
   EMAIL_PASSWORD=your-app-password
   FLASK_SECRET_KEY=any-secret-string-for-sessions
   ```

   Optional:

   - `EMAIL_POLL_INTERVAL=10` – seconds between email checks (default: 10)
   - `AUTO_REPLY_ENABLED=true` – send auto-reply when a new ticket is created (default: true)

4. **Create MongoDB indexes (once)**

   ```bash
   python create_indexes.py
   ```

## How to run

**Web dashboard** (view tickets, reply, create tickets):

```bash
python app.py
```

Then open: **http://localhost:5005**

**Email fetching** (turn incoming emails into tickets, send acks):

```bash
python email_fetching.py
```

Leave this running in a separate terminal so it keeps polling the inbox. Use the dashboard to log in (add an admin/agent in MongoDB first, or use any email on first run when the DB is empty).
