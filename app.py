from flask import Flask, render_template, request, redirect, url_for, jsonify, Response, stream_with_context, abort, session

from pdf_routes import install_pdf_routes

from dotenv import load_dotenv

from openai import OpenAI

import os

from web_security import install_security, safe_markdown

from flask_sqlalchemy import SQLAlchemy


import json
import secrets
import httpx
import click
from email_auth import install_email_auth

import time

from werkzeug.security import generate_password_hash, check_password_hash

from flask_login import LoginManager, UserMixin, login_user, current_user, login_required, logout_user

from datetime import datetime
from production_config import configure
from quiz_randomization import randomized_question
from usage_install import install_usage
from ai_service import prime_stream
from usage_ledger import LimitReached

load_dotenv()

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
configure(app)

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    if user and email_auth["verified"](user) and session.get("auth_version", 0) == email_auth["version"](user):
        return user
    return None

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    courses = db.relationship(
        "Course",
        backref="user",
        lazy="select"
    )

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    lectures = db.relationship(
        "Lecture",
        backref="course",
        lazy="select",
        cascade="all, delete-orphan"
    )

class Lecture(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    course_id = db.Column(
        db.Integer, 
        db.ForeignKey("course.id"), 
        #二つのテーブルを関連付ける　親はCourse
        nullable=False
    )
    week = db.Column(db.Integer, nullable=False)

    messages = db.relationship(
    "ChatMessage",
    backref="lecture",
    cascade="all, delete-orphan"
)

    pdf_filename = db.Column(db.String(255))

    summary = db.Column(db.Text)

    text = db.Column(db.Text)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    lecture_id = db.Column(
        db.Integer,
        db.ForeignKey("lecture.id")
    )

    role = db.Column(db.String(20))
    content = db.Column(db.Text)

class QuizAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    lecture_id = db.Column(
        db.Integer,
        db.ForeignKey("lecture.id"),
        nullable=False
    )

    score = db.Column(db.Integer, nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)

    completed_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.now
    )

    lecture = db.relationship(
        "Lecture",
        backref=db.backref("quiz_attempts", cascade="all, delete-orphan")
    )

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"), timeout=120, max_retries=0
)

email_auth = install_email_auth(app, db, User)
pdf_service = install_pdf_routes(app, db, Lecture)
lecture_owner = install_security(app, db, Lecture)
ai_service = install_usage(app, db, client)

@app.cli.command("init-db")
def init_db():
    """Create missing tables without changing existing records."""
    if os.getenv('APP_ENV') == 'production':
        raise click.ClickException('Use alembic upgrade head for production schema changes')
    db.create_all()

@app.get("/terms")
def terms():
    return render_template("terms.html")


@app.get("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email_auth["valid_email"](email) or not 8 <= len(password) <= 128:
            return render_template("register.html", error="正しいメールアドレスと8〜128文字のパスワードを入力してください。"), 400
        if email_auth["find_user"](email):
            return email_auth["notice"]("既に登録されている場合は、ログインまたは認証メールの再送をお試しください。")
        user = User(email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()
        db.session.add(email_auth["State"](user_id=user.id, verified=False))
        db.session.commit()
        try:
            email_auth["send_link"](user, "verify")
        except (httpx.HTTPError, RuntimeError):
            db.session.rollback()
            app.logger.error("Registration email delivery failed")
            return email_auth["notice"]("アカウントを作成しましたが、メールを送信できませんでした。認証メールの再送をお試しください。", 503)
        return email_auth["notice"]("認証メールを送信しました。24時間以内にメールのリンクを開いて認証してください。")
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = email_auth["find_user"](email)

        if user and check_password_hash(user.password_hash, password):
            if not email_auth["verified"](user):
                return email_auth["notice"]("メールアドレスの認証が必要です。受信箱を確認するか、認証メールを再送してください。", 403)
            session.clear()
            session["auth_version"] = email_auth["version"](user)
            login_user(user)
            return redirect(url_for("home"))

        return "メールアドレスまたはパスワードが違います", 401

    return render_template("login.html")

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/menu")
def menu():
    return render_template("menu.html")

def latest_quiz_percentages(course_ids):
    ranked = (
        db.session.query(
            Lecture.course_id,
            QuizAttempt.lecture_id,
            QuizAttempt.score,
            QuizAttempt.total_questions,
            db.func.row_number().over(
                partition_by=QuizAttempt.lecture_id,
                order_by=(QuizAttempt.completed_at.desc(), QuizAttempt.id.desc())
            ).label("rank")
        )
        .join(Lecture, Lecture.id == QuizAttempt.lecture_id)
        .filter(Lecture.course_id.in_(course_ids))
        .subquery()
    )
    return db.session.query(ranked).filter(ranked.c.rank == 1).all()


@app.route("/course", methods=["GET", "POST"])
@login_required
def course():
    if request.method == "POST":
        course_name = request.form["course_name"]

        new_course = Course(
            name=course_name,
            user_id=current_user.id
        )

        db.session.add(new_course)
        db.session.flush()

        for week in range(1, 16):
            lecture = Lecture(
                course_id=new_course.id,
                week=week
            )
            db.session.add(lecture)

        db.session.commit()
        return redirect(url_for("course"))

    courses = Course.query.filter_by(
        user_id=current_user.id
    ).all()

    # Courseごとの平均得点率
    percentages = {item.id: [] for item in courses}
    if courses:
        for attempt in latest_quiz_percentages(list(percentages)):
            percentages[attempt.course_id].append(
                attempt.score / attempt.total_questions * 100
            )
    course_average_scores = {
        course_id: round(sum(scores) / len(scores)) if scores else None
        for course_id, scores in percentages.items()
    }

    if "course_delete_token" not in session:
        session["course_delete_token"] = secrets.token_hex(32)

    return render_template(
        "course.html",
        courses=courses,
        course_average_scores=course_average_scores
    )

@app.route("/course/<int:course_id>/delete", methods=["POST"])
@login_required
def delete_course(course_id):
    course = Course.query.filter_by(id=course_id).with_for_update().populate_existing().first()
    if course is None:
        abort(404)
    if course.user_id != current_user.id:
        abort(403)

    token = session.get("course_delete_token")
    if not token or not secrets.compare_digest(
        token, request.form.get("csrf_token", "")
    ):
        abort(400)

    for item in course.lectures:
        pdf_service["enqueue"](item.pdf_filename)
    db.session.delete(course)
    db.session.commit()
    pdf_service["cleanup"]()
    return redirect(url_for("course"))


@app.route("/lecture/<int:course_id>")
@login_required
def lecture(course_id):
    course = db.session.get(Course, course_id)

    if course is None:
        abort(404)

    if course.user_id != current_user.id:
        abort(403)

    lectures = (
        db.session.query(Lecture.id, Lecture.week, Lecture.pdf_filename)
        .filter(Lecture.course_id == course_id)
        .order_by(Lecture.week.asc(), Lecture.id.asc())
        .all()
    )

    latest_scores = {item.id: None for item in lectures}
    for attempt in latest_quiz_percentages([course_id]):
        latest_scores[attempt.lecture_id] = round(
            attempt.score / attempt.total_questions * 100
        )

    return render_template(
        "lecture.html",
        course=course,
        lectures=lectures,
        latest_scores=latest_scores
    )

@app.route("/lecture/<int:lecture_id>/upload", methods=["GET", "POST"])
@login_required
def upload_lecture(lecture_id):
    lecture = db.session.get(Lecture, lecture_id)

    if lecture is None:
        abort(404, description="講義が見つかりません")

    if lecture.course.user_id != current_user.id:
        abort(403, description="アクセスが許可されていません")

    if request.method == "POST":
        return pdf_service["upload"](lecture)

    # ---------- GETの場合 ----------

    messages = ChatMessage.query.filter_by(
        lecture_id=lecture.id
    ).all()

    for message in messages:
        message.content_html = safe_markdown(message.content)

    summary_html = safe_markdown(lecture.summary)

    quiz_attempts = (
        QuizAttempt.query
        .filter_by(lecture_id=lecture.id)
        .order_by(QuizAttempt.completed_at.asc())
        .all()
    )

    quiz_score_data = []

    for attempt in quiz_attempts:
        quiz_score_data.append({
            "label": attempt.completed_at.strftime("%m/%d %H:%M"),
            "score": round(
                attempt.score / attempt.total_questions * 100
            )
        })

    return render_template(
        "upload.html",
        lecture=lecture,
        summary_html=summary_html,
        messages=messages,
        quiz_score_data=quiz_score_data
    )

def generate_summary_stream(lecture, instruction=None):
    if not lecture.text or not lecture.text.strip():
        raise LimitReached('先に文字を抽出できるPDFをアップロードしてください。', 400)

    prompt = f"""
あなたは大学教授です。

以下の授業資料を分析してください。
回答は以下の授業資料に記載されている内容だけを根拠にしてください。
資料にない情報を推測・補完しないでください。
必ずMarkdown形式で出力してください。
余計な前置きや説明は不要です。
必ず「## 要約」から出力を開始してください。

## 要約
300文字程度

## 重要語句

以下の形式で出力してください。
同じ用語を重複して出力しないでください。

- **用語名**：説明

## 試験に出そうなポイント

- 箇条書き

## 覚えるべき年号

授業資料に明記されている年号・年月日のみ抽出してください。
条文番号（例：第96条）やページ番号など、
年号・年月日ではない数字は含めないでください。

資料に存在しない年号を推測・補完してはいけません。

統計資料の集計時点やデータベースの基準日など、
授業内容そのものを理解するうえで重要でない日付は含めないでください。

年号がない場合は、
表を出力せず「該当なし」としてください。

| 年号 | 出来事 |
|------|--------|

授業資料：

{lecture.text}
"""

    if instruction:
        prompt += f"""

追加指示：

{instruction}

追加指示にも従いつつ、
授業資料にない情報を推測・補完しないでください。
"""

    summary_parts = []

    stream = ai_service.stream('summary',
        model="gpt-5.4-mini",
        reasoning={
            "effort": "medium"
        },
        input=prompt,
        stream=True
    )

    for event in stream:

        if event.type == "response.output_text.delta":

            summary_parts.append(event.delta)

            yield event.delta

    summary = "".join(summary_parts)

    # ストリーミング開始前のオブジェクトはセッションから外れるため、再取得する。
    lecture = db.session.get(Lecture, lecture.id)
    if lecture is None:
        return
    lecture.summary = summary
    db.session.commit()

@app.route("/lecture/<int:lecture_id>/summary-stream", methods=["POST"])
@lecture_owner
def stream_summary(lecture_id):

    lecture = Lecture.query.get(lecture_id)

    return Response(
        stream_with_context(
            prime_stream(generate_summary_stream(lecture))
        ),
        mimetype="text/plain"
    )
@app.route("/lecture/<int:lecture_id>/reanalyze", methods=["POST"])
@lecture_owner
def reanalyze_lecture(lecture_id):

    lecture = Lecture.query.get(lecture_id)

    instruction = request.form.get(
        "instruction",
        ""
    ).strip()

    return Response(
        stream_with_context(
            prime_stream(generate_summary_stream(
                lecture,
                instruction
            ))
        ),
        mimetype="text/plain"
    )

@app.route("/lecture/<int:lecture_id>/chat", methods=["POST"])
@lecture_owner
def chat(lecture_id):
    lecture = Lecture.query.get(lecture_id)
    if not lecture.text or not lecture.text.strip():
        raise LimitReached('先に文字を抽出できるPDFをアップロードしてください。', 400)

    message = request.form.get("message", "").strip()
    if not message:
        raise LimitReached('質問を入力してください。', 400)

    user_message = ChatMessage(
        lecture_id=lecture.id,
        role="user",
        content=message
    )

    chat_history = ChatMessage.query.filter_by(
        lecture_id=lecture.id
    ).order_by(ChatMessage.id.asc()).all()

    history_messages = []

    for chat_message in chat_history:
        history_messages.append({
            "role": chat_message.role,
            "content": chat_message.content
        })

    openai_messages = [
        {
            "role": "system",
            "content": f"""
あなたは大学教授です。

以下の授業資料を参考に、
ユーザーの質問に答えてください。

回答はMarkdown形式で記述してください。
見出しや通常の説明文、出典、URLなどを不必要に箇条書きにしないでください。

１つの説明を「項目名」「要点」「出典」などに細かく分割して、
それぞれを別々の箇条書きにしないでください。

空の箇条書き（「-」だけの行）は作成しないでください。

見出し、箇条書き、表、コードブロック、引用を
内容に応じて適切に使い分けてください。

授業資料：
{lecture.text}
"""
        }
    ]

    openai_messages.extend(history_messages)
    openai_messages.append({'role': 'user', 'content': message})

    def generate():

        reply_parts = []

        stream = ai_service.stream('chat',
            model="gpt-5.4-nano",
            reasoning={
                "effort": "low"
            },
            input=openai_messages,
            stream=True
        )
        # Store only accepted questions: quota/input rejection leaves no chat entry.
        db.session.add(user_message)
        db.session.commit()

        for event in stream:

            if event.type == "response.output_text.delta":

                reply_parts.append(event.delta)

                yield event.delta

        reply = "".join(reply_parts)

        assistant_message = ChatMessage(
            lecture_id=lecture.id,
            role="assistant",
            content=reply
        )

        db.session.add(assistant_message)
        db.session.commit()

    return Response(
        stream_with_context(prime_stream(generate())),
        mimetype="text/plain"
    )

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    abort(404)

@app.route("/lecture/<int:lecture_id>/quiz/generate", methods=["POST"])
@lecture_owner
def generate_quiz(lecture_id):

    lecture = Lecture.query.get(lecture_id)
    if not lecture.text or not lecture.text.strip():
        raise LimitReached('先に文字を抽出できるPDFをアップロードしてください。', 400)

    def generate():

        buffer = ""

        stream = ai_service.stream('quiz',
            model="gpt-5.4-nano",
            reasoning={
                "effort": "medium"
            },
            input=f"""
あなたは大学教授です。

以下の授業資料から復習テストを10問作成してください。

必ず1行につき1問のJSONだけを出力してください。
JSON配列にはしないでください。
Markdownや前置きは不要です。

例：

{{"type":"multiple_choice","question":"問題文","choices":["選択肢1","選択肢2","選択肢3","選択肢4"],"answer":"正解","explanation":"解説"}}

{{"type":"true_false","question":"問題文","answer":true,"explanation":"解説"}}

{{"type":"keyword","question":"問題文","answer":"短い語句","explanation":"解説"}}

問題作成ルール：
・問題形式は multiple_choice、true_false、keyword の3種類のみを使用してください。
・multiple_choice は4択問題にしてください。
・true_false は正誤問題にしてください。
・問題数は4択4問、正誤4問、キーワード2問にしてください。出題順は混ぜてください。
・正誤4問は正解trueを2問、falseを2問にしてください。falseの問題文は資料に基づく明確な誤りを含め、解説で正しい内容を説明してください。
・正解の値だけを反転してはいけません。問題文・正解・解説の内容を一致させてください。
・keyword は、専門用語・人名・制度名・出来事の名称などを答える問題にしてください。
・keyword の answer は、必ず単語または短い語句にしてください。
・keyword の answer を文章にしないでください。
・記述式（written）は出題せず、説明を求めたい内容はキーワード問題や正誤問題に置き換えてください。
・合計で10問にしてください。
・授業資料に書かれていない内容を推測して問題にしないでください。

授業資料：
{lecture.text}
""",
            stream=True
        )

        for event in stream:

            if event.type == "response.output_text.delta":

                buffer += event.delta

                while "\n" in buffer:

                    line, buffer = buffer.split("\n", 1)

                    line = line.strip()

                    if line:
                        yield randomized_question(line) + "\n"

        buffer = buffer.strip()

        if buffer:
            yield randomized_question(buffer) + "\n"

    return Response(
        stream_with_context(prime_stream(generate())),
        mimetype="application/x-ndjson"
    )

@app.route("/lecture/<int:lecture_id>/quiz/keyword/check", methods=["POST"])
@lecture_owner
def check_keyword(lecture_id):

    user_answer = request.form.get("user_answer")
    correct_answer = request.form.get("correct_answer")
    if not isinstance(user_answer, str) or not user_answer.strip() or not correct_answer:
        raise LimitReached('回答と模範解答を入力してください。', 400)


    response = ai_service.completion('grading',
        model="gpt-5.4-nano",
        messages=[
            {
                "role": "user",
                "content": f"""
    のユーザー回答が、模範解答と同じ語句・人物・出来事を
    単なる表記揺れで表しているか判定してください。
    カタカナ転写、長音、表記方法などの違いは正解としてください。
    ただし、別の人物・用語・出来事や、意味を説明しただけの回答は不正解です。

    模範解答：
    {correct_answer}

    ユーザー回答：
    {user_answer}

    表記揺れとして正解なら true、
    不正解なら false のみを出力してください。
    """
            }
        ]
    )

    ai_result = response.choices[0].message.content.strip()


    return jsonify({
        "is_correct": ai_result.lower() == "true"
    })

@app.route("/lecture/<int:lecture_id>/quiz/written/check", methods=["POST"])
@lecture_owner
def check_written(lecture_id):

    user_answer = request.form.get("user_answer")
    model_answer = request.form.get("model_answer")
    rubric = request.form.get("rubric")
    try:
        rubric = json.loads(rubric or '')
    except (TypeError, ValueError):
        raise LimitReached('採点基準の形式が不正です。', 400) from None
    if not user_answer or not user_answer.strip() or not model_answer or not isinstance(rubric, list):
        raise LimitReached('回答・模範解答・採点基準を確認してください。', 400)
    # 文字列から配列に


    response = ai_service.completion('grading',
        model="gpt-5.4-nano",
        response_format={"type": "json_object"}, 
        messages=[
            {
                "role": "user",
                "content": f"""
    あなたは大学教授です。
    学生の記述式問題の回答を厳格に採点してください。

    模範解答：
    {model_answer}

    採点基準：
    {rubric}

    学生の回答：
    {user_answer}

    採点ルール：
    ・採点基準の各項目を学生の回答が満たしているか確認してください。
    ・単に意味が近いだけで、必要な論点が書かれていない場合は満たしたと判定しないでください。
    ・学生の回答に書かれていない内容を推測して補わないでください。
    ・部分的に触れているだけの場合は、満たしたと判定しないでください。

    必ず次のJSON形式のみで回答してください。

    {{
        "is_correct": true,
        "score": 3,
        "total": 3,
        "feedback": "採点についての説明"
    }}
    """
            }
        ]
    )

    ai_result = response.choices[0].message.content
    ai_result = json.loads(ai_result)
    
    return jsonify(ai_result)
    
@app.route("/lecture/<int:lecture_id>/quiz/result", methods=["POST"])
@login_required
def save_quiz_result(lecture_id):
    lecture = db.session.get(Lecture, lecture_id)

    if lecture is None:
        abort(404)

    if lecture.course.user_id != current_user.id:
        abort(403)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Invalid result"), 400

    score = data.get("score")
    total_questions = data.get("total_questions")

    if type(score) is not int or type(total_questions) is not int:
        return jsonify({"error": "Invalid score"}), 400

    if total_questions <= 0 or score < 0 or score > total_questions:
        return jsonify({"error": "Invalid score"}), 400

    attempt = QuizAttempt(
        lecture_id=lecture.id,
        score=score,
        total_questions=total_questions
    )

    db.session.add(attempt)
    db.session.commit()

    return jsonify({
        "success": True,
        "attempt_id": attempt.id,
        "label": attempt.completed_at.strftime("%m/%d %H:%M"),
        "score": round(attempt.score / attempt.total_questions * 100)
    })

if __name__ == "__main__":
    if os.getenv('APP_ENV') == 'production':
        raise RuntimeError('Use Gunicorn for production and Alembic for schema changes')
    with app.app_context():
        db.create_all()
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
