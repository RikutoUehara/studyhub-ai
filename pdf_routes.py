"""PDF validation and durable cleanup across independent DB/R2 transactions."""
from io import BytesIO
import os
import secrets
import time
import uuid

import click
from botocore.exceptions import BotoCoreError, ClientError
from flask import abort, jsonify, request, session, Response, stream_with_context, url_for
from flask_login import current_user, login_required
from pypdf import PdfReader
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from pdf_storage import R2Storage


def install_pdf_routes(app, db, Lecture):
    class PdfDeletion(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        key = db.Column(db.String(255), nullable=False, unique=True)
        not_before = db.Column(db.Float, nullable=False, default=0)

    app.config["PDF_MAX_BYTES"] = int(os.getenv("PDF_MAX_BYTES", 10 * 1024 * 1024))
    app.config["PDF_MAX_PAGES"] = int(os.getenv("PDF_MAX_PAGES", 200))
    app.config["MAX_CONTENT_LENGTH"] = app.config["PDF_MAX_BYTES"] + 1024 * 1024

    def storage():
        if "pdf_storage" not in app.extensions:
            app.extensions["pdf_storage"] = R2Storage()
        return app.extensions["pdf_storage"]

    def csrf_token():
        if "pdf_csrf" not in session:
            session["pdf_csrf"] = secrets.token_urlsafe(32)
        return session["pdf_csrf"]

    app.jinja_env.globals["pdf_csrf_token"] = csrf_token

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        return jsonify(success=False, error="ファイルサイズが上限を超えています。"), 413

    def enqueue(key):
        if key and not PdfDeletion.query.filter_by(key=key).first():
            db.session.add(PdfDeletion(key=key, not_before=0))

    def cleanup(limit=100, detailed=False):
        # Queue rows are committed before external operations. Failure leaves a retryable job.
        try:
            ids = [row.id for row in PdfDeletion.query.filter(PdfDeletion.not_before <= time.time()).limit(limit)]
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.error("PDF cleanup unavailable; retry later")
            return {'completed': 0, 'failed': 1} if detailed else 0
        completed = 0
        failed = 0
        for job_id in ids:
            try:
                job = PdfDeletion.query.filter_by(id=job_id).with_for_update().first()
                if job is None or job.not_before > time.time():
                    db.session.rollback()
                    continue
                if Lecture.query.filter_by(pdf_filename=job.key).first():
                    db.session.delete(job)
                else:
                    storage().delete(job.key)
                    app.extensions['abuse_control'].pdf_deleted(job.key)
                    db.session.delete(job)
                db.session.commit()
                completed += 1
            except (BotoCoreError, ClientError, RuntimeError, SQLAlchemyError):
                db.session.rollback()
                app.logger.error("PDF cleanup failed; job retained for retry")
                failed += 1
        return {'completed': completed, 'failed': failed} if detailed else completed

    @app.cli.command("cleanup-pdfs")
    def cleanup_command():
        """Retry due R2 deletion jobs (run periodically in production)."""
        click.echo(f"Completed PDF cleanup jobs: {cleanup()}")

    def upload(lecture):
        expected = session.get("pdf_csrf", "")
        if not expected or not secrets.compare_digest(expected, request.form.get("csrf_token", "")):
            return jsonify(success=False, error="画面を再読み込みして再度お試しください。"), 400
        file = request.files.get("pdf")
        if file is None or not file.filename or not file.filename.lower().endswith(".pdf"):
            return jsonify(success=False, error="PDFファイルを選択してください。"), 400
        data = file.stream.read(app.config["PDF_MAX_BYTES"] + 1)
        if len(data) > app.config["PDF_MAX_BYTES"]:
            return jsonify(success=False, error="PDFのサイズが上限を超えています。"), 413
        try:
            if not data.startswith(b"%PDF-"):
                raise ValueError()
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted or not 0 < len(reader.pages) <= app.config["PDF_MAX_PAGES"]:
                raise ValueError()
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return jsonify(success=False, error="読み取れるPDFを指定してください（暗号化PDF・ページ数超過は非対応）。"), 400

        lecture_id, user_id = lecture.id, current_user.id
        Course = type(lecture.course)
        course_id = lecture.course_id
        key = f"users/{user_id}/lectures/{lecture_id}/{uuid.uuid4()}.pdf"
        # Journal first: a crash during PUT or a failed DB commit cannot lose the cleanup key.
        try:
            db.session.commit()
            job_id = app.extensions['abuse_control'].pdf_reserve(
                user_id, key, len(data), cleanup_model=PdfDeletion)
            storage().put(key, data)
            # Course deletion uses the same parent lock; take it first to avoid lock inversion.
            course = Course.query.filter_by(id=course_id).with_for_update().populate_existing().first()
            if course is None or course.user_id != user_id:
                raise RuntimeError("Upload target no longer exists")
            job = PdfDeletion.query.filter_by(id=job_id).with_for_update().first()
            lecture = Lecture.query.filter_by(id=lecture_id).with_for_update().populate_existing().first()
            if job is None or lecture is None or lecture.course.user_id != user_id:
                raise RuntimeError("Upload target no longer exists")
            old_key = lecture.pdf_filename
            lecture.pdf_filename, lecture.text = key, text
            usage = app.extensions['usage_ledger']
            db.session.get(usage.m['PdfObject'], key).status = 'stored'
            # Old analysis belongs to the previous PDF.
            lecture.summary = None
            lecture.messages.clear()
            lecture.quiz_attempts.clear()
            enqueue(old_key)
            db.session.delete(job)
            db.session.commit()
        except (BotoCoreError, ClientError, RuntimeError, SQLAlchemyError):
            db.session.rollback()
            app.logger.error("PDF upload failed; pending objects will be cleaned up")
            return jsonify(success=False, error="PDFを保存できませんでした。時間をおいて再度お試しください。"), 503
        cleanup()
        return jsonify(success=True, pdf_url=url_for("lecture_pdf", lecture_id=lecture_id), text=text)

    @app.route("/lecture/<int:lecture_id>/pdf")
    @login_required
    def lecture_pdf(lecture_id):
        lecture = db.session.get(Lecture, lecture_id)
        if lecture is None:
            abort(404)
        if lecture.course.user_id != current_user.id:
            abort(403)
        if not lecture.pdf_filename:
            abort(404)
        try:
            body = storage().get(lecture.pdf_filename)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                abort(404)
            abort(503)
        except (BotoCoreError, RuntimeError):
            abort(503)

        def chunks():
            try:
                while True:
                    chunk = body.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                body.close()

        disposition = "attachment" if request.args.get("download") == "1" else "inline"
        response = Response(stream_with_context(chunks()), mimetype="application/pdf")
        response.headers["Content-Disposition"] = f'{disposition}; filename="lecture-{lecture_id}.pdf"'
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.call_on_close(body.close)
        return response

    return {"upload": upload, "enqueue": enqueue, "cleanup": cleanup, "Deletion": PdfDeletion}
