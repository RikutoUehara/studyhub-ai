# 本番DB移行結果（2026-10-14 日本時間）

ユーザーの個別承認に基づき実行しました。実行直前、ユーザーがRender Auto-Deploy Off・Web Service Suspended・backup-before-migration-20261014の存在と期限内であることを管理画面で確認しました。管理APIでの独立確認は未実施です。

接続先はローカルDATABASE_URL・Neon production比較用URI・Render比較用URIの3者一致を再確認し、usage-testとは別であることを確認しました。接続URI・パスワードは出力していません。

- READ ONLYで既存8テーブルと固定0001のスキーマを検証: PASS。
- 0001_baseline適用: PASS。
- 0002_usage_controls適用: PASS。alembic_versionは0002_usage_controls。
- 既存8テーブルの全レコードを各適用後と容量初期化後にメモリ内比較: 変更なし。本文・個人情報・PDFキーはログに出力していません。
- 追加7テーブルと初期制御状態: PASS。初期状態はstopped=true、pdf_ready=false、月次台帳0件。
- usage-init-pdfs: PASS。PDF1件、529175 bytes。PDFオブジェクト合計とユーザー容量合計が一致。
- 最終状態: stopped=true、pdf_ready=true。AI停止解除は未実施。

既存件数はuser 3、course 1、lecture 15、email_auth 3、email_token 15、chat_message 0、quiz_attempt 0、pdf_deletion 0です。

R2クライアントをHEAD専用でラップし、読み取り本文・書込み・削除メソッドを拒否して容量初期化を実行しました。実OpenAI・Resend呼出し、R2書込み・削除、commit・push・Renderデプロイ・サービス再開・Cron開始は行っていません。

過去の準備文書の「本番未移行」「容量未初期化」は本結果で更新されます。Webの旧コードは停止したままです。次は利用制限版コードのcommit/push・デプロイを個別承認後に実行し、AI停止中で確認してください。旧コードをそのまま再開すると停止スイッチを無視するため、旧版をAI有効で公開しないでください。
