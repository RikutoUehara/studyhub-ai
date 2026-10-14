# v0.1.0の本番起動

RenderなどのLinux環境でGunicornを使います。サイトの公開はまだ行っていません。

## デプロイ先の設定

- Build command: `pip install -r requirements.txt`
- 初回・追加テーブル導入時: `python -m flask --app app init-db`
- Start command: `gunicorn --config gunicorn.conf.py app:app`
- Health check path: `/healthz`（プロセスの応答確認。DB・R2の疎通確認ではありません）

初期化コマンドはデプロイ先のシェルまたはpre-deploy処理から実行します。
現在のDBには追加テーブルも作成済みです。

`.env.example` の値をサービスの環境変数へ登録してください。
本番では `APP_ENV=production`、`FLASK_DEBUG=0`、
`PUBLIC_BASE_URL=https://tanitorutorukun.com` を使います。
最初にホスティングの提供URLで試す場合は、そのHTTPS URLを設定してください。
`SECRET_KEY` は32文字以上のランダムな値にし、再デプロイでも固定します。
DB接続はSSLを使用します。秘密情報をGitやデプロイログへ出さないでください。

Windowsのローカル開発では `APP_ENV=development` のまま
`venv/Scripts/python.exe app.py` で起動できます。
デバッグが必要なときだけ `FLASK_DEBUG=1` を設定します。
GunicornはWindowsにはインストールしません。

## PDFの削除再試行

同じ環境変数を設定した定期ジョブから、5分ごとを目安に以下を実行します。

```sh
python -m flask --app app cleanup-pdfs
```

## 本番の動作確認

ドメインをホスティングの案内に従って接続し、HTTPSを有効にします。
スマホから登録・メール認証・パスワード再設定を確認してください。
PDF保存・再起動後の閲覧・チャット・復習テスト・授業削除も確認します。
AIのストリーミングやPDF転送はスレッドを占有します。
利用状況とメモリに合わせてワーカー数・スレッド数を調整してください。

認証リンクのトークンがURLに含まれるため、Gunicornのアクセスログは無効です。
ホスティング側のログ収集も確認してください。
ストリーム開始後のAPI障害は、通常の500エラー応答へ切り替えられません。

公開前の残課題は、ログイン・メール・AI処理の回数制限と利用上限、
利用規約・プライバシー説明、成績を正式に扱う場合のサーバー側採点です。
