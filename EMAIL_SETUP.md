# 単位トルトル君のメール認証

`.env` に以下を設定します（APIキーはGitに含めないでください）。

```dotenv
RESEND_API_KEY=your-key
MAIL_FROM="単位トルトル君 <noreply@tanitorutorukun.com>"
MAIL_REPLY_TO=aiknowledgehub.support@gmail.com
PUBLIC_BASE_URL=http://localhost:5000
```

本番では `PUBLIC_BASE_URL` を利用者がアクセスできるサイトのHTTPS URLに変更してください。
送信元はResendで認証したドメインに合わせてください。
`SECRET_KEY` は固定の十分に長いランダムな値を使用してください。

初回とデプロイ時に、追加テーブルを作成してからアプリを起動します。
既存のユーザーテーブルやデータを変更する操作はありません。

```powershell
venv/Scripts/python.exe -m flask --app app init-db
venv/Scripts/python.exe app.py
```

新規登録後はメールのリンクを開き「認証する」を押してからログインします。
認証リンクは24時間、パスワード再設定リンクは30分で期限切れになります。
リンクは一度だけ使用でき、同じ用途のメール再送は60秒間隔です。
メール送信失敗後もログイン画面から認証メールを再送できます。
パスワード変更後は既存のログインセッションも無効になります。
追加前に登録済みのユーザーは認証済みとして扱います。

送信API: https://resend.com/docs/api-reference/emails/send-email

検証（テスト用メモリDBとメール送信モックを使用）:

```powershell
venv/Scripts/python.exe -m unittest discover -s tests -v
```
