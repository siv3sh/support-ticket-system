"""Tests for auth and role-based access."""
import os
import pytest
from unittest.mock import patch, MagicMock

# Set env before importing app
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test_support")


@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c


def test_health_check(client):
    """Health endpoint returns 200 or 503 depending on DB."""
    r = client.get("/health")
    # 200 if DB connected, 503 if not
    assert r.status_code in (200, 503)
    data = r.get_json()
    assert "status" in data
    assert "database" in data


def test_login_page(client):
    """Login page loads."""
    r = client.get("/login")
    assert r.status_code == 200
    assert b"Sign in" in r.data or b"Login" in r.data


def test_forgot_password_page(client):
    """Forgot password page loads."""
    r = client.get("/forgot-password")
    assert r.status_code == 200
    assert b"Forgot" in r.data or b"reset" in r.data


def test_ticket_list_requires_login(client):
    """Tickets list redirects to login when not authenticated."""
    r = client.get("/tickets", follow_redirects=False)
    assert r.status_code == 302
    assert "login" in r.location
