# v0.1.0の本番起動

RenderなどのLinux環境でGunicornを使います。既存版はRenderへデプロイ済みですが、利用制限版の本番反映は未実施です。

## デプロイ先の設定

- Build command: `pip install -r requirements.txt`
- 利用制限導入後のDB移行: `python -m alembic upgrade head`（詳細は `USAGE_CONTROLS.md`）
- Start command: `gunicorn --config gunicorn.conf.py app:app`
- Health check path: `/healthz`（プロセスの応答確認。DB・R2の疎通確認ではありません）

初期化コマンドはデプロイ先のシェルまたはpre-deploy処理から実行します。
2026-10-14に本番DB移行とPDF容量初期化は完了しました（`PRODUCTION_MIGRATION_RESULT.md`）。AIは停止中です。通常の起動・ビルドでDB初期化や移行を繰り返さないでください。

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

## 統合定期処理

DB移行・PDF容量初期化の完了後、同じ環境変数を設定したRender Cron Jobを1つ作成し、10分ごとを目安に以下を実行します。詳細は `RENDER_CRON.md` を参照してください。旧PDF削除ジョブと重複稼働させないでください。

```sh
python -m flask --app app usage-maintenance
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

利用制限の設定と定期処理は `USAGE_CONTROLS.md` を参照してください。
公開前の残課題は利用規約・プライバシー説明と、本番での制限検証です。
