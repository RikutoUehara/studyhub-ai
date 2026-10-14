# 新版コードのRender反映前確認

## 確認済み

- ローカルブランチ: release/v0.1.0。
- origin: https://github.com/RikutoUehara/studyhub-ai.git。
- 利用制限版は未commit・未push。既存HEADは527982c。
- .envと.cacheはGit除外。変更対象にローカル.envの既知秘密値の一致0件。これは任意の秘密値漏洩を完全保証する検査ではない。
- ローカルのGunicorn設定は1 worker / 4 gthread / timeout180、PORT利用。開始コマンドはgunicorn --config gunicorn.conf.py app:app。
- requirements.txtにAlembic/tiktokenが追加済み、GunicornはLinuxでインストールされる。
- 本番DBは0002適用・PDF容量初期化済み・AI停止中。再移行やinit-dbは今回のビルド/起動コマンドへ追加しない。
- 全52テスト再実行成功（123.130秒、usage-test隔離スキーマとモック）。SQLAlchemy非推奨警告・SQLite接続ResourceWarningは残る。
- ローカル設定をAPP_ENV=productionにしたアプリ初期化・利用制限設定検証: PASS。実際のRender環境値は未確認。pip check: 依存関係の破損なし。

## Render管理画面で未確認

実際の参照ブランチ・Build/Start Command・プラン・全環境変数は管理API未接続のため独立に検証できない。Auto-Deploy Off / Suspendedはユーザーが前回直前確認した状態。新たな状態の確認を依頼中。

必須設定（値を共有しない）:

- APP_ENV=production、FLASK_DEBUG=0。
- SECRET_KEY（32文字以上、既存値を維持）、DATABASE_URL（照合済み本番先）。
- OPENAI_API_KEY、RESEND_API_KEY、MAIL_FROM。
- R2_ACCOUNT_ID、R2_ACCESS_KEY_ID、R2_SECRET_ACCESS_KEY、R2_BUCKET_NAME。
- PUBLIC_BASE_URL（実際のサービスの公開HTTPS URL）。
- AI_ADMIN_EMAIL（通知先、Cron開始前に必須）、AI_BUDGET_JPY=3000、AI_WARNING_JPY=1800、AI_STOP_JPY=2400、AI_USD_JPY=200。
- AI_MODEL_PRICES={"gpt-5.4-mini":[0.75,4.5],"gpt-5.4-nano":[0.2,1.25]}。

AI料金・予算にはコードの既定値があるため未設定だけで起動失敗するわけではない。実際のモデルが料金表に含まれることと上書き値の妥当性を確認する。AI_ADMIN_EMAIL未設定は通常Web起動を阻害しないがCron通知は失敗する。ローカル設定がRenderへ自動転送されることはない。

USAGE_TEST_DATABASE_URL、MIGRATION_TEST_DATABASE_URL、比較用URI、管理APIキーをRenderのアプリ設定へコピーしない。MAIL_REPLY_TOは既存設定を維持する。

## 旧版を公開しない切替

Render Deploy Hooksの公式資料はSuspendedサービスで409を返すと記載している。Manual Deploy/Resumeの全経路で停止中にコード更新できることは未確認。旧サービスをResumeしてから新版を展開する手順は採用しない。

確実な候補は **旧サービスをSuspendedのまま保持し、承認された新版コミットから別Web Serviceを作成する** 方法。追加サービスの料金とURL変更への承認が必要。新サービスは指定した新版SHAであることを確認し、最初からAuto-Deploy Off、新版設定、既存本番DB/R2を使用する。新サービス作成自体がデプロイを開始するため、作成前にデプロイ承認を得る。AI停止設定は維持する。

新URLになる場合、PUBLIC_BASE_URLとメール内リンクを新URLに合わせる。独自ドメインを使用しているなら別途承認して切り替える。旧サービスは削除せず停止を維持する。失敗時は新サービスを停止して調査し、旧AIコードは再公開しない。

既存サービスを再利用する場合は、旧版を起動・公開せずに指定SHAへ切り替えるRenderの具体的操作が確認できた場合だけ採用する。Resumeで起動する版を推測しない。

## 操作別承認

1. 秘密値を除外した変更ファイルを明示してrelease/v0.1.0へcommit。
2. originの同ブランチへpush（追跡先の更新確認、force pushなし）。Render Auto-Deploy Offを再確認。
3. 確定したサービス/コミットへの新版デプロイ。新サービスの場合は料金・新URL・設定も明示。
4. AI停止中にhealthz、ログイン、利用状況の停止表示、既存PDF・保存済み要約を確認。
5. AI停止解除は別承認。Cron作成・実APIテスト・R2削除処理開始も別承認。

今回はcommit/push/デプロイ/再開/停止解除を実行していない。

公式資料: https://render.com/docs/deploy-hooks
