"""Shared authorization, CSRF validation and Markdown sanitization."""
from functools import wraps
import secrets

import markdown
import nh3
from flask import abort, jsonify, request, session
from flask_login import current_user, login_required


def safe_markdown(value):
    return nh3.clean(markdown.markdown(value or "", extensions=["tables", "fenced_code"]))


def install_security(app, db, Lecture):
    protected = {"course", "logout", "stream_summary", "reanalyze_lecture", "chat",
                 "generate_quiz", "check_keyword", "check_written", "save_quiz_result"}

    def csrf_token():
        if "security_csrf" not in session:
            session["security_csrf"] = secrets.token_urlsafe(32)
        return session["security_csrf"]

    app.jinja_env.globals["security_csrf_token"] = csrf_token

    @app.before_request
    def protect_updates():
        if request.method == "POST" and request.endpoint in protected:
            if not current_user.is_authenticated:
                return app.login_manager.unauthorized()
            expected = session.get("security_csrf", "")
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            if not expected or not secrets.compare_digest(expected, supplied):
                return jsonify(error="画面を再読み込みして、もう一度お試しください。"), 400

    def lecture_owner(function):
        @wraps(function)
        @login_required
        def checked(lecture_id, *args, **kwargs):
            lecture = db.session.get(Lecture, lecture_id)
            if lecture is None:
                abort(404)
            if lecture.course.user_id != current_user.id:
                abort(403)
            return function(lecture_id, *args, **kwargs)
        return checked

    return lecture_owner
