from flask import Flask, render_template, request, redirect, url_for, jsonify

from pypdf import PdfReader

from dotenv import load_dotenv

from openai import OpenAI

import os

import markdown

from flask_sqlalchemy import SQLAlchemy

from flask import send_from_directory

import json

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

@app.route("/lecture/<int:lecture_id>/quiz/generate", methods=["POST"])
def generate_quiz(lecture_id):
    lecture = Lecture.query.get(lecture_id)

    response = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[
            {
                "role": "user",
                "content": f"""
                あなたは大学教授です。

                以下の授業資料から、復習テストを作成してください。

                必ずJSON形式のみで出力してください。
                前置きやMarkdownは不要です。

                形式は次の通りです。

                {{
                    "questions": [
                        {{
                            "type": "multiple_choice",
                            "question": "問題文",
                            "choices": ["選択肢1", "選択肢2", "選択肢3", "選択肢4"],
                            "answer": "正解",
                            "explanation": "解説"
                        }},
                        {{
                            "type": "true_false",
                            "question": "問題文",
                            "answer": true,
                            "explanation": "解説"
                        }},
                        {{
                            "type": "keyword",
                            "question": "問題文",
                            "answer": "短い語句",
                            "explanation": "解説"
                        }},
                        {{
                            "type": "written",
                            "question": "問題文",
                            "model_answer": "模範解答",
                            "rubric": [
                                "必須論点1",
                                "必須論点2",
                                "必須論点3"
                            ]
                        }}
                    ]
                }}
                問題作成ルール：
                ・multiple_choice は4択問題にしてください。
                ・true_false は正誤問題にしてください。
                ・keyword は、専門用語・人名・制度名・出来事の名称などを答える問題にしてください。
                ・keyword の answer は、必ず単語または短い語句にしてください。
                ・keyword の answer を文章にしないでください。
                ・written は、文章で説明する必要がある問題にしてください。
                ・合計で10問になるようにしてください。

                授業資料：
                {lecture.text}
                """
            }
        ]
    )

    quiz = response.choices[0].message.content
    quiz_data = json.loads(quiz)

    return jsonify(
        quiz_data
    )

@app.route("/lecture/<int:lecture_id>/quiz/keyword/check", methods=["POST"])
def check_keyword(lecture_id):

    user_answer = request.form.get("user_answer")
    correct_answer = request.form.get("correct_answer")

    print("ユーザー回答:", user_answer)
    print("模範解答:", correct_answer)

    response = client.chat.completions.create(
        model="gpt-5-nano",
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

    print("AI判定:", ai_result)

    return jsonify({
        "is_correct": ai_result.lower() == "true"
    })

@app.route("/lecture/<int:lecture_id>/quiz/written/check", methods=["POST"])
def check_written(lecture_id):

    user_answer = request.form.get("user_answer")
    model_answer = request.form.get("model_answer")
    rubric = request.form.get("rubric")
    rubric = json.loads(rubric)
    # 文字列から配列に

    print("ユーザー回答:", user_answer)
    print("模範解答:", model_answer)
    print("採点基準:", rubric)

    response = client.chat.completions.create(
        model="gpt-5-nano",
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
    print("AIの生回答", ai_result)
    ai_result = json.loads(ai_result)
    print("AI採点結果:", ai_result)
    
    return jsonify(ai_result)
    


if __name__ == "__main__":

    with app.app_context():
        db.create_all()
    app.run(debug=True)
