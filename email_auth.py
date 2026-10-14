"""Email authentication; separate tables preserve existing user records."""
import hashlib
import os
import re
import secrets
import time
from urllib.parse import urlsplit

import httpx
from email_delivery import send_email
from flask import abort, render_template, request, session, url_for
from werkzeug.security import generate_password_hash


def install_email_auth(app, db, User):
    class EmailAuth(db.Model):
        user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
        verified = db.Column(db.Boolean, nullable=False, default=False)
        session_version = db.Column(db.Integer, nullable=False, default=0)

    class EmailToken(db.Model):
        digest = db.Column(db.String(64), primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
        purpose = db.Column(db.String(20), nullable=False)
        created_at = db.Column(db.Float, nullable=False)
        expires_at = db.Column(db.Float, nullable=False)
        used = db.Column(db.Boolean, nullable=False, default=False)

    app.config["PUBLIC_BASE_URL"] = os.getenv("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")

    def csrf_token():
        if "auth_csrf" not in session:
            session["auth_csrf"] = secrets.token_urlsafe(32)
        return session["auth_csrf"]

    app.jinja_env.globals["auth_csrf_token"] = csrf_token

    @app.before_request
    def protect_auth_forms():
        if request.method == "POST" and request.endpoint in {
            "register", "login", "forgot_password", "reset_password", "resend_verification", "verify_email"
        }:
            expected = session.get("auth_csrf", "")
            if not expected or not secrets.compare_digest(expected, request.form.get("csrf_token", "")):
                abort(400, "画面を再読み込みして、もう一度お試しください。")
            abuse = app.extensions.get('abuse_control')
            if abuse and request.endpoint in {'login', 'register', 'forgot_password', 'resend_verification'}:
                abuse.auth_request(request.endpoint)

    @app.after_request
    def private_auth_pages(response):
        if request.endpoint in {"verify_email", "reset_password", "forgot_password", "resend_verification"}:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def find_user(email):
        return User.query.filter(db.func.lower(User.email) == email.strip().lower()).first()

    def verified(user):
        state = db.session.get(EmailAuth, user.id)
        return state is None or state.verified

    def version(user):
        state = db.session.get(EmailAuth, user.id)
        return state.session_version if state else 0

    def notice(message, status=200):
        return render_template("auth_message.html", message=message), status

    def send_link(user, purpose):
        recent = EmailToken.query.filter_by(user_id=user.id, purpose=purpose).filter(
            EmailToken.created_at > time.time() - 60
        ).first()
        if recent:
            return max(1, int(recent.created_at + 60 - time.time()) + 1)
        key, sender = os.getenv("RESEND_API_KEY"), os.getenv("MAIL_FROM")
        base = app.config["PUBLIC_BASE_URL"]
        parsed = urlsplit(base)
        if not key or not sender or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("Email settings are incomplete")
        app.extensions['abuse_control'].email_send(user.id, purpose)
        token = secrets.token_urlsafe(32)
        lifetime = 86400 if purpose == "verify" else 1800
        record = EmailToken(digest=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                            purpose=purpose, created_at=time.time(), expires_at=time.time() + lifetime)
        endpoint = "verify_email" if purpose == "verify" else "reset_password"
        link = base + url_for(endpoint, token=token)
        action = "メールアドレスの認証" if purpose == "verify" else "パスワードの再設定"
        payload = {"from": sender, "to": [user.email], "subject": f"【単位トルトル君】{action}",
                   "text": f"単位トルトル君をご利用いただきありがとうございます。\n\n{action}はこちら：\n{link}\n\n有効期限は{'24時間' if purpose == 'verify' else '30分'}です。\n心当たりがない場合は、このメールを無視してください。"}
        if os.getenv("MAIL_REPLY_TO"):
            payload["reply_to"] = os.getenv("MAIL_REPLY_TO")
        send_email(payload, record.digest)
        db.session.add(record)
        db.session.commit()

    def get_token(token, purpose):
        record = db.session.get(EmailToken, hashlib.sha256(token.encode()).hexdigest())
        if not record or record.purpose != purpose or record.used or record.expires_at <= time.time():
            return None
        return record

    def consume(record):
        count = EmailToken.query.filter_by(digest=record.digest, used=False).filter(
            EmailToken.expires_at > time.time()
        ).update({"used": True}, synchronize_session=False)
        return count == 1

    @app.route("/verify-email/<token>", methods=["GET", "POST"])
    def verify_email(token):
        record = get_token(token, "verify")
        if not record:
            return notice("リンクが無効または期限切れです。認証メールを再送してください。", 400)
        if request.method == "GET":
            return render_template("auth_form.html", title="メールアドレスの認証", mode="verify", button="認証する")
        if not consume(record):
            db.session.rollback()
            return notice("このリンクは使用済みです。", 400)
        state = db.session.get(EmailAuth, record.user_id)
        state.verified = True
        EmailToken.query.filter_by(user_id=record.user_id, purpose="verify").update({"used": True})
        db.session.commit()
        return notice("メールアドレスを認証しました。ログインしてください。")

    @app.route("/resend-verification", methods=["GET", "POST"])
    def resend_verification():
        return request_email("verify")

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        return request_email("reset")

    def request_email(purpose):
        if request.method == "GET":
            return render_template("auth_form.html", title="認証メールの再送" if purpose == "verify" else "パスワードの再設定",
                                   mode="email", button="メールを送信")
        user = find_user(request.form.get("email", ""))
        if user and (purpose == "reset" or not verified(user)):
            try:
                wait_seconds = send_link(user, purpose)
                if wait_seconds:
                    response, status = notice(
                        f"メールの再送には60秒以上の間隔をあける必要があります。あと{wait_seconds}秒待ってから、再度お試しください。",
                        429,
                    )
                    response = app.make_response((response, status))
                    response.headers["Retry-After"] = str(wait_seconds)
                    return response
            except (httpx.HTTPError, RuntimeError):
                db.session.rollback()
                app.logger.error("Authentication email delivery failed")
                return notice("メールを送信できませんでした。時間をおいて再度お試しください。", 503)
        return notice("該当するアカウントがある場合、メールを送信しました。受信箱をご確認ください。")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        record = get_token(token, "reset")
        if not record:
            return notice("リンクが無効または期限切れです。再設定メールを再度送信してください。", 400)
        if request.method == "GET":
            return render_template("auth_form.html", title="新しいパスワード", mode="password", button="パスワードを変更")
        password = request.form.get("password", "")
        if not 8 <= len(password) <= 128 or password != request.form.get("password_confirm"):
            return render_template("auth_form.html", title="新しいパスワード", mode="password", button="パスワードを変更",
                                   error="8〜128文字の一致するパスワードを入力してください。"), 400
        if not consume(record):
            db.session.rollback()
            return notice("このリンクは使用済みです。", 400)
        user = db.session.get(User, record.user_id)
        user.password_hash = generate_password_hash(password)
        state = db.session.get(EmailAuth, user.id)
        if state is None:
            state = EmailAuth(user_id=user.id, verified=True, session_version=0)
            db.session.add(state)
        state.session_version += 1
        EmailToken.query.filter_by(user_id=user.id, purpose="reset").update({"used": True})
        db.session.commit()
        session.clear()
        return notice("パスワードを変更しました。新しいパスワードでログインしてください。")

    return {"State": EmailAuth, "find_user": find_user, "verified": verified, "version": version,
            "send_link": send_link, "notice": notice,
            "valid_email": lambda email: len(email) <= 100 and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email))}
