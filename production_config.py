"""Deployment settings shared by development and production."""
import logging
import os
from urllib.parse import urlsplit

from flask import jsonify, request


def configure(app):
    production = os.getenv("APP_ENV", "development") == "production"
    if production:
        secret = app.config.get("SECRET_KEY") or ""
        public_url = urlsplit(os.getenv("PUBLIC_BASE_URL", ""))
        required = ("DATABASE_URL", "OPENAI_API_KEY", "RESEND_API_KEY", "MAIL_FROM",
                    "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
        if len(secret) < 32 or any(not os.getenv(name) for name in required):
            raise RuntimeError("Production environment settings are incomplete")
        if public_url.scheme != "https" or not public_url.hostname or public_url.hostname in {"localhost", "127.0.0.1"}:
            raise RuntimeError("PUBLIC_BASE_URL must be a public HTTPS URL")
    app.config.update(
        DEBUG=False,
        SESSION_COOKIE_SECURE=production,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 300 if production else -1},
    )
    app.logger.setLevel(logging.INFO)

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify(error="処理に失敗しました。時間をおいて再度お試しください。"), 500

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.path != "/healthz" and not request.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response
