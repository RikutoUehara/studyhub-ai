import os
import re
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["OPENAI_API_KEY"] = "test-key"
os.environ["RESEND_API_KEY"] = "test-key"
os.environ["MAIL_FROM"] = "test@example.com"

from app import app, db, User, email_auth


class EmailAuthTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, PUBLIC_BASE_URL="https://example.com")
        self.context = app.app_context()
        self.context.push()
        db.create_all()
        control = db.session.get(app.extensions['usage_ledger'].m['Control'], 1)
        control.stopped, control.pdf_ready = False, True
        db.session.commit()
        self.client = app.test_client()
        self.messages = []
        self.sender = patch("email_auth.httpx.post", side_effect=self.capture)
        self.sender.start()

    def capture(self, *args, **kwargs):
        self.messages.append(kwargs["json"])
        response = unittest.mock.Mock()
        response.raise_for_status.return_value = None
        return response

    def tearDown(self):
        self.sender.stop()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def post(self, path, **data):
        from flask import g
        g.pop("_login_user", None)
        self.client.get(path)
        with self.client.session_transaction() as state:
            data["csrf_token"] = state["auth_csrf"]
        return self.client.post(path, data=data)

    def register(self):
        return self.post("/register", email="student@example.com", password="old-password")

    def link(self):
        return re.search(r"https://example.com([^\s]+)", self.messages[-1]["text"]).group(1)

    def test_verification_and_reset(self):
        self.assertEqual(self.register().status_code, 200)
        self.assertEqual(self.post("/login", email="student@example.com", password="old-password").status_code, 403)
        verification = self.link()
        self.assertEqual(self.client.get(verification).status_code, 200)
        self.assertFalse(email_auth["verified"](User.query.first()))
        self.assertEqual(self.post(verification).status_code, 200)
        self.assertEqual(self.client.get(verification).status_code, 400)
        self.assertEqual(self.post("/login", email="student@example.com", password="old-password").status_code, 302)
        self.post("/forgot-password", email="student@example.com")
        reset = self.link()
        self.assertEqual(self.post(reset, password="new-password", password_confirm="different").status_code, 400)
        self.assertEqual(self.post(reset, password="new-password", password_confirm="new-password").status_code, 200)
        self.assertEqual(self.client.get(reset).status_code, 400)
        self.assertEqual(self.post("/login", email="student@example.com", password="old-password").status_code, 401)
        self.assertEqual(self.post("/login", email="student@example.com", password="new-password").status_code, 302)

    def test_expiry_purpose_csrf_and_cooldown(self):
        self.assertEqual(self.client.post("/register", data={}).status_code, 400)
        self.register()
        link = self.link()
        self.assertEqual(self.client.get(link.replace("verify-email", "reset-password")).status_code, 400)
        response = self.post("/resend-verification", email="student@example.com")
        self.assertEqual(response.status_code, 429)
        self.assertIn("60秒以上の間隔", response.get_data(as_text=True))
        self.assertTrue(1 <= int(response.headers["Retry-After"]) <= 60)
        self.assertEqual(len(self.messages), 1)
        with patch("email_auth.time.time", return_value=__import__("time").time() + 61):
            self.assertEqual(self.post("/resend-verification", email="student@example.com").status_code, 200)
        self.assertEqual(len(self.messages), 2)
        with patch("email_auth.time.time", return_value=10**12):
            self.assertEqual(self.client.get(link).status_code, 400)
        self.assertEqual(self.client.get("/reset-password/invalid").status_code, 400)

    def test_other_session_invalidated_and_existing_users_preserved(self):
        from werkzeug.security import generate_password_hash
        user = User(email="student@example.com", password_hash=generate_password_hash("old-password"))
        db.session.add(user)
        db.session.commit()
        self.assertTrue(email_auth["verified"](user))
        self.post("/login", email=user.email, password="old-password")
        other = app.test_client()
        with other.session_transaction() as state:
            state["_user_id"] = str(user.id)
            state["auth_version"] = 0
        self.assertEqual(other.get("/").status_code, 200)
        self.post("/forgot-password", email=user.email)
        self.post(self.link(), password="new-password", password_confirm="new-password")
        from flask import g
        g.pop("_login_user", None)
        self.assertEqual(other.get("/").status_code, 302)

    def test_delivery_failure_can_be_retried(self):
        import httpx
        with patch("email_auth.httpx.post", side_effect=httpx.ConnectError("offline")):
            self.assertEqual(self.register().status_code, 503)
        self.assertFalse(email_auth["verified"](User.query.first()))
        with patch('abuse_control.time.time', return_value=__import__('time').time() + 61):
            self.assertEqual(self.post("/resend-verification", email="student@example.com").status_code, 200)
        self.assertEqual(len(self.messages), 1)


if __name__ == "__main__":
    unittest.main()
