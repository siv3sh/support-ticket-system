# Support Ticket System

A support ticket system: emails become tickets, and you can view and reply from a web dashboard. Includes role-based access, user management, and optional AI reply suggestions.

## Stack

- **Python 3** – app and scripts
- **Flask** – web dashboard (login, tickets, reply, users, profile)
- **MongoDB** – tickets, customers, users, audit logs
- **Gmail (IMAP/SMTP)** – fetch new emails and send replies (run `email_service.py`)

## Roles

- **Admin** – Full access; create users, manage tickets, view all.
- **Agent** – View tickets, create tickets, reply, change status, suggest reply.
- **Viewer** – View tickets only (no reply or status change).
- **Customer** – View and add comments only on tickets linked to their contact email.

## Setup

1. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

2. **Environment variables**  
   Copy `.env.example` to `.env` and set:

   - **Flask:** `FLASK_SECRET_KEY`, optional `FLASK_ENV`, `PORT`
   - **MongoDB:** Either `MONGODB_URI` or `MONGODB_HOST`, `MONGODB_PORT`, `MONGODB_USERNAME`, `MONGODB_PASSWORD`, `MONGODB_AUTH_SOURCE`, `MONGODB_DATABASE`
   - **Email:** `EMAIL_ADDRESS`, `EMAIL_PASSWORD` (Gmail: use an App Password)
   - Optional: `GEMINI_API_KEY` (for AI reply suggestions), `EMAIL_POLL_INTERVAL`, `AUTO_REPLY_ENABLED`, `TICKETS_PER_PAGE`, `SESSION_LIFETIME_MINUTES`
   - **First-time setup:** When the DB has no users, set `ALLOW_FIRST_TIME_SETUP=1` or `FLASK_ENV=development` to allow any email to log in as admin once (no password). Then create real users from the dashboard.

3. **Create MongoDB indexes (once)**

   ```bash
   python create_indexes.py
   ```

## Run

**Web dashboard** (tickets, users, profile):

```bash
python app.py
```

Open **http://localhost:5005** (or the port set by `PORT` in `.env`).

**Email fetching** (turn incoming emails into tickets, send acks):

```bash
python email_service.py
```

Run this in a separate terminal to keep polling the inbox.

## Features

- **Tickets:** List (with pagination and filters), create, view, reply, change status. Optional “Suggest reply” (similar past tickets + Gemini fallback).
- **Users (Admin):** Add user (name, email, phone, address, designation, role). Option to send a temporary password by email. Edit user, activate/deactivate.
- **Profile:** Update name, phone, address, designation. Change password.
- **Forgot password:** Request reset link by email; set new password via link (valid 1 hour).
- **Audit log:** Login, logout, ticket create/reply/status, user create/edit/deactivate, password reset are logged in the `audit_logs` collection.
- **Health:** `GET /health` returns 200 if the app and MongoDB are OK (for load balancers).

## Security

- CSRF protection on all forms (Flask-WTF).
- Login rate limit (5 per minute per IP).
- Session: HTTPOnly, SameSite=Lax, optional Secure in production.
- First-time no-password setup only when `ALLOW_FIRST_TIME_SETUP=1` or `FLASK_ENV=development`.
- Debug mode is off unless `FLASK_ENV=development` or `DEBUG=1`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Production

- Set `FLASK_ENV=production` and a strong `FLASK_SECRET_KEY`.
- Use a production WSGI server (e.g. gunicorn): `gunicorn -w 4 -b 0.0.0.0:5005 app:app`
- Configure MongoDB and email via `.env`; do not commit `.env`.
