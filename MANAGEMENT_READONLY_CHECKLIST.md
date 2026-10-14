# 管理画面の読み取り専用確認

2026-10-14時点。確認だけを行い、Save、Deploy、Suspend、Delete、Restore、Reset password、Create branch等は押さないでください。個人情報・接続URI・キー・ログ全文はチャットへ貼らないでください。

## 接続手段の調査結果

- PATH上にrender/neon/neonctl/psql/pg_dump/ghは見つかりません。npm/npxはありますが、グローバルnpmディレクトリも見つかりませんでした。
- プロセス環境と.envにRENDER_API_KEY/RENDER_API_TOKEN/NEON_API_KEY/NEON_API_TOKENはありません。
- Render標準の ~/.render/cli.yaml とNeonの標準・旧版候補ディレクトリに認証設定はありません。別の場所に保存された認証まで存在しないとは断定しません。
- Render・Neon連携は未接続です。CLI導入だけでは管理認証は得られないため、未認証のCLIインストールは行っていません。
- DATABASE_URLはSQL接続用で、Neon管理APIのAPIキーではありません。R2のS3キーもCloudflare管理設定の確認権限とは別です。
- 既存のDB/R2読み取り確認はRELEASE_PREPARATION.mdを参照してください。

自動確認を続ける場合、Render/Neon連携を接続するか、管理APIキーをローカル.envへRENDER_API_KEY/NEON_API_KEYとして設定する方法があります。値をチャットへ送る必要はありません。これらは運用確認用で、Web Service/Cronの実行には不要です。設定後も今回はGETとSQL読み取りのみ使用し、管理APIの書込みは行いません。キーの作成自体はユーザーの判断で行ってください。

## Render

1. Dashboardで対象Web Serviceを開く。サービス名・ID・URL・Live/Suspended等の状態を控える。ワークスペース内に同じアプリの別サービス、プレビュー、Worker、Cronがあるかも確認する。
2. Deploysで現在Liveのデプロイを開き、コミットSHAと成功日時を控える。Settingsのブランチだけでは稼働コミットの証拠にならない。
3. SettingsでRepository、Branch、Auto-Deploy、Build Command、Start Command、Health Check Path、Instance Type/プランを確認する。変更・保存はしない。
4. Environmentで変数名の存在だけ確認する。DATABASE_URLの接続先ホスト・DB名がローカル.envの接続先と同一か、画面内で比較する。パスワード・完全URIを共有しない。R2_BUCKET_NAMEの一致、APP_ENV、PUBLIC_BASE_URL、AI_*設定、AI_ADMIN_EMAILの有無も確認する。Linked Environment GroupsとSecret Filesも確認し、個別設定との重複があるか記録する。
5. SettingsのCustom Domainsを確認する。旧onrender.com URLも残るため、DNS切替だけを旧サービスの停止と考えない。
6. Logsは自分の画面で起動・エラーを確認する。共有する場合は日時とエラー分類だけにし、URLトークン・メール・Cookie・本文等を含む全文を貼らない。
7. 既存Cronがあれば、そのサービスでコマンド・スケジュール・参照ブランチを確認する。今はTrigger Runしない。既存cleanup-pdfsと新しい統合Cronは重複稼働させない。

共有してほしい情報:

```text
Renderサービス名/ID:
稼働コミットSHA:
参照ブランチ:
Auto-Deploy:
プラン/Instance Type:
Build Command:
Start Command:
Health Check Path:
DATABASE_URLのローカル接続先との一致: はい/いいえ/未確認
R2_BUCKET_NAMEの一致: はい/いいえ/未確認
別Web Service/Worker/Cron: なし/あり（コマンドだけ）
独自ドメイン:
```

## Neon

1. Consoleで対象プロジェクトを選び、プロジェクト名・IDと契約プランを確認する。
2. Branchesでproductionとusage-testを開き、ブランチID・親ブランチ/rootかどうか・現在の状態を確認する。名前だけで本番接続先を断定しない。
3. productionのConnect画面で接続先・database名をローカルDATABASE_URLと比較する。pooledとdirectではホストに-poolerの差がある。完全URIは共有しない。Render側との一致も必要。
4. Settings → Postgres → History windowを開き、現在の保持期間を確認する。画面名称が異なる場合はInstant restore/History windowを探す。設定変更・Saveはしない。
5. Backup/Restore/Snapshotsに相当する画面で、利用可能な復旧手段、復旧可能な時刻、既存バックアップがあるか確認する。RestoreやCreateは押さない。root/子ブランチとプランによって使える手段が異なる。
6. バックアップ方法が未確定ならその旨を共有する。件数確認はバックアップではなく、DBバックアップもR2のPDFを復元しない。

共有してほしい情報:

```text
Neonプロジェクト名/ID:
契約プラン:
productionブランチID・root/親ブランチ:
usage-testとの分離: 確認済み/未確認
Render・ローカル接続先との一致: はい/いいえ/未確認
History window:
利用可能なバックアップ/復旧方法:
既存の復旧用バックアップ: あり/なし/未確認
```

## R2（未確認設定だけ）

Cloudflare Dashboard → R2 → tanitorutorukun-pdf → Settingsで、Public Development URLとCustom Domainsの公開状態を確認してください。両方の状態を共有し、変更しないでください。S3 HEAD成功だけでは非公開設定を証明できません。

## 管理者通知の設定箇所

受信先を決めた後、ローカル.envに以下を設定します。今は空欄のままで構いません。

```dotenv
AI_ADMIN_EMAIL=管理者の受信用メールアドレス
AI_WARNING_JPY=1800
AI_STOP_JPY=2400
AI_BUDGET_JPY=3000
```

送信には既存のRESEND_API_KEYとMAIL_FROMを使用します。MAIL_REPLY_TOは受信先指定ではありません。管理者は既存ユーザーとは別の宛先を指定しても構いません。

本番反映の承認後、Render Cron Job → Environment → Add Environment VariableでAI_ADMIN_EMAILを設定します。Web Serviceにも同じ設定を揃えるとShellからの通知コマンドにも利用できます。個別設定または適切なEnvironment Groupを選び、重複定義を避けてください。

今は本番設定を変更しないでください。後で保存する際もSave and deployはデプロイを起動します。Save onlyは稼働中プロセスへ即反映しません。環境グループのリンク・変更がデプロイを起動する場合もあります。

月の推定合計（実績・予約・課金不明）が注意基準に到達すると、usage-maintenanceの通知処理が送信を試みます。未設定なら通知処理は失敗しCronは非ゼロ終了します。他の定期処理は可能な限り継続します。宛先指定は本番Cronを開始する前に必要です。

## 承認前に確定する内容

管理画面情報がそろったら、既存サービス更新か新サービス切替か、対象サービスID、対象コミット、バックアップ方法、書込み遮断方法、移行順序、切り戻し条件を確定します。その後commit/push・本番DB変更・停止・デプロイ・削除処理について必要な承認を求めます。現時点では操作対象が未確定なので本番操作の承認を求めません。

## 公式資料

- Render CLI: https://render.com/docs/cli
- Render API: https://render.com/docs/api
- Render環境変数: https://render.com/docs/configure-environment-variables
- Neon CLI: https://neon.com/docs/cli
- Neon History window: https://neon.com/docs/postgres/backup-restore/history-window
