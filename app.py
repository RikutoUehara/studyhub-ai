from flask import Flask, render_template, request, redirect, url_for, jsonify

from pypdf import PdfReader

from dotenv import load_dotenv

from openai import OpenAI

import os

import markdown

from flask_sqlalchemy import SQLAlchemy

from flask import send_from_directory

load_dotenv()

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///study.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

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
    backref="lecture"
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

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/menu")
def menu():
    return render_template("menu.html")

@app.route("/course", methods=["GET", "POST"])
def course():
    if request.method == "POST":
        course_name = request.form["course_name"]
        course = Course(name=course_name)
        db.session.add(course)
        db.session.commit()

        for week in range(1, 16):
            lecture = Lecture(course_id=course.id, 
                              week=week)
            db.session.add(lecture)
        db.session.commit()

    courses = Course.query.all()
    return render_template("course.html", 
                           courses=courses)

@app.route("/lecture/<int:course_id>")
def lecture(course_id):
    course = Course.query.get(course_id)
    lectures = Lecture.query.filter_by(course_id=course_id).all()
    return render_template(
        "lecture.html", 
        course=course, 
        lectures=lectures
    )

@app.route("/lecture/<int:lecture_id>/upload", methods=["GET", "POST"])
def upload_lecture(lecture_id):
    lecture = Lecture.query.get(lecture_id)

    summary_html = markdown.markdown(
        lecture.summary or "", 
        extensions=["tables", "fenced_code"]
    )
    text = ""
    summary = ""

    if request.method == "POST":
        pdf = request.files["pdf"]

        if pdf.filename == "":
            return render_template(
                "upload.html", 
                lecture=lecture, 
                summary_html= summary_html, 
                error="ファイルを選択してください"
            )
            
        filename = pdf.filename
        filepath = os.path.join("uploads", filename)
        pdf.save(filepath)
        lecture.pdf_filename = filename
        db.session.commit()
        reader = PdfReader(filepath)
        
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

        lecture.text = text

        response = client.chat.completions.create(
                    model="gpt-5-nano",
                    messages=[
                        {"role": "user",
                         "content": f"""
                         あなたは大学教授です。

以下の授業資料を分析してください。
必ずMarkdown形式で出力してください。
余計な前置きや説明は不要です。
必ず「## 要約」から出力を開始してください。

## 要約
300文字程度

## 重要語句
- 用語
- 説明

## 試験に出そうなポイント
- 箇条書き

## 覚えるべき年号
| 年号 | 出来事 |
|------|--------|

## 確認問題
1.
2.
3.
4.
5.

授業資料:
{text}"""
                         }
                    ]
                )
        summary = response.choices[0].message.content
        summary_html = markdown.markdown(
            summary, 
            extensions=["tables", "fenced_code"]
        )
        lecture.summary = summary
        db.session.commit()

    messages = ChatMessage.query.filter_by(
        lecture_id=lecture.id
    ).all()

    for message in messages:
        message.content_html = markdown.markdown(
            message.content or "",
            extensions=["tables", "fenced_code"]
        )

    return render_template(
        "upload.html", 
        lecture=lecture,
        summary=summary,
        summary_html=summary_html,
        text=text, 
        messages=messages
    )

@app.route("/lecture/<int:lecture_id>/reanalyze", methods=["POST"])
def reanalyze_lecture(lecture_id):
    lecture = Lecture.query.get(lecture_id)

    instruction = request.form["instruction"].strip()
    if instruction == "":
        prompt = f"""
        あなたは大学教授です。
        以下の授業資料を分析してください。
        必ずMarkdown形式で出力してください。

        ## 要約
        300文字程度

        ## 重要語句
        - 用語
        - 説明

        ## 試験に出そうなポイント
        - 箇条書き

        ## 覚えるべき年号
        | 年号 | 出来事 |
        |------|--------|

        ## 確認問題
        1.
        2.
        3.
        4.
        5.

        授業資料：
        {lecture.text}
        """
    else:
        # 追加指示がある場合
        prompt = f"""
        あなたは大学教授です。
        以下の追加指示を最優先してください。

        追加指示：
        {instruction}

        回答は必ずMarkdown形式で出力してください。

        授業資料：
        {lecture.text}
        """

    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    lecture.summary = response.choices[0].message.content
    db.session.commit()

    return redirect(url_for("upload_lecture", lecture_id=lecture.id))

@app.route("/lecture/<int:lecture_id>/chat", methods=["POST"])
def chat(lecture_id):
    lecture = Lecture.query.get(lecture_id)

    message = request.form["message"]

    print(message)

    user_message = ChatMessage(
    lecture_id=lecture.id,
    role="user",
    content=message
    )

    db.session.add(user_message)
    db.session.commit()

    chat_history = ChatMessage.query.filter_by(
    lecture_id=lecture.id
    ).order_by(ChatMessage.id.asc()).all()

    history_messages = []

    for chat in chat_history:
        history_messages.append({
            "role": chat.role,
            "content": chat.content
        })

    openai_messages = [
    {
        "role": "system",
        "content": f"""
        あなたは大学教授です。

        以下の授業資料を参考に、
        ユーザーの質問に答えてください。

        回答はMarkdown形式で記述してください。
        見出しや通常の説明文、出展、URLなどを不必要に箇条書きにしないでください。

        １つの説明を「項目名」「要点」「出展」などに細かく分割して、それぞれを別々の箇条書きにしないでください。

        空の箇条書き(「-」だけの行)は作成しないでください。

        見出し、箇条書き、表、コードブロック、引用を内容に応じて適切に使い分けてください。

        授業資料：
        {lecture.text}
        """
    }
    ]

    openai_messages.extend(history_messages)


    response = client.chat.completions.create(
    model="gpt-5-nano",
    messages= openai_messages
    )

    reply = response.choices[0].message.content
    reply_html = markdown.markdown(
    reply or "",
    extensions=["tables", "fenced_code"]
    )

    print(repr(reply))

    assistant_message = ChatMessage(
        lecture_id=lecture.id,
        role="assistant",
        content=reply
        )

    db.session.add(assistant_message)
    db.session.commit()

    return jsonify({
    "reply": reply, 
    "reply_html": reply_html
    })

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory("uploads", filename)



if __name__ == "__main__":

    with app.app_context():
        db.create_all()
    app.run(debug=True)
