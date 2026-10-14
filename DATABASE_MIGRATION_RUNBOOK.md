# productionバックアップ・復元試験・DB移行

これは準備用手順です。バックアップ作成、復元、本番変更は未実施です。各操作の承認後に実行します。

## 現時点の事実と未確定事項

ユーザーの管理画面確認: プロジェクトAI Knowledge Hub、production（Default）、usage-test（Schema-only）、production履歴6時間、スナップショット0件。RenderのAuto-Deployは当初On Commit。現在Offかどうかは未確認。

ローカルDATABASE_URLとUSAGE_TEST_DATABASE_URLは別のホスト・DBの組合せで、ローカルDATABASE_URLにSSL要求があります。ローカル接続先の既存スキーマ検証は成功していますが、Neonブランチ名は管理認証または画面照合がないため未確定です。Defaultとrootは別概念です。usage-testは本番データのバックアップではありません。

## 秘密値を出さない3者照合

1. NeonでAI Knowledge Hub → production → Connectを開き、対象DBを選択する。接続URIをチャットへ送らず、ローカル.envの一時変数 `NEON_PRODUCTION_COMPARE_URL` に保存する。スクリーンショットにも含めない。
2. `venv/Scripts/python.exe scripts/database_identity.py` を実行する。`NEON_PRODUCTION_COMPARE_URL` の matches_local_DATABASE_URL がtrueなら、ローカル接続先がそのproduction接続先と一致する。
3. Renderの稼働中Shellで、次の読み取り専用コードをpythonに入力する。接続・SQL実行はせず、実際のプロセスのDATABASE_URLの接続先ハッシュだけ表示する。

```python
import os, json, hashlib
from urllib.parse import urlsplit, unquote
u = urlsplit(os.environ['DATABASE_URL'])
identity = [(u.hostname or '').lower().replace('-pooler.', '.'), u.port or 5432, unquote(u.path.lstrip('/'))]
print(hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest())
```

4. RenderのハッシュとローカルスクリプトのDATABASE_URLハッシュが一致するか確認する。Shellが使えない場合、Render EnvironmentのDATABASE_URLをローカル.envの `RENDER_DATABASE_COMPARE_URL` に保存して同じスクリプトで照合する。ただし保存済み設定が稼働中プロセスに反映されているかは別途デプロイ設定と照合する。
5. 比較結果だけ共有する。3者一致を確認するまで本番接続先を確定扱いしない。一時比較変数は照合後に削除できる。ホストの-pooler差は正規化し、認証情報・クエリ設定は比較対象外。これは接続先の照合であり、ユーザー権限・SSL設定の一致検証ではない。

## バックアップの選択

第一候補はデータを含むバックアップブランチです。6時間の履歴だけに依存せず、移行時点のコピーを残します。Branch作成自体は今は実行しません。

承認後、書込みと削除ジョブを停止してからNeon → Branches → New branchで、Parentをproduction、Includeを **Current data**、名前を `pre-usage-YYYYMMDD-HHMM` 等にします。**Schema onlyは選びません。** 自動削除設定を確認し、移行と復元試験が終わる前に期限が来ないようにします。料金・ブランチ上限も確認します。作成時刻、親・子ID、対象DBを控え、バックアップブランチにはアプリを接続せず、変更・リセットしません。

補助候補はpg_dumpのcustom-formatです。現在pg_dump/pg_restoreはPATH上にありません。承認後にPostgreSQLクライアントを準備し、サーバーバージョンに対応したpg_dumpと互換性のあるpg_restoreを使います。direct（unpooled）接続を使用します。接続情報はPGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD/PGSSLMODEなどをPythonから子プロセス環境に渡し、コマンド引数へ完全URIやパスワードを埋めません。

実行形（認証環境を安全に設定した後）:

```text
pg_dump --format=custom --no-owner --no-acl --file=<保護されたバックアップファイル>
pg_restore --list <バックアップファイル>
```

バックアップにはメール・パスワードハッシュ・PDF抽出本文などが含まれます。Git管理外・OneDrive同期外のアクセス制限された場所に保存し、必要なら暗号化します。終了コード、ファイルサイズ、アーカイブ検査に加え、復元試験で有効性を確認します。ログやアーカイブ一覧の全文はチャットへ貼りません。pg_dumpはDB全体の論理バックアップですが、クラスタの全ロール設定やR2実体を含みません。

## 復元できることの確認

ブランチ方式:

1. バックアップブランチから別の `restore-check-YYYYMMDD-HHMM` ブランチをCurrent dataで作る。元バックアップは変更しない。
2. 本番・usage-test・バックアップ・復元試験の各接続先を照合し、試験先が本番と異なることを強制チェックする。
3. コピー時点の基準と比較する。全既存テーブルの件数、主キー集合、外部キー、授業/講義の所有関係、PDFキーと本文・要約・認証関連データの一致をローカルで照合する。結果は一致/不一致と件数だけ記録し、内容・メール・キーは出力しない。IDシーケンスも確認する。
4. 復元試験先に0001 → 0002を適用して、既存データが変わらないこと、AI停止中・PDF未初期化、再実行がno-opであることを確認する。PDF容量初期化はR2 HEADモックで試験する。
5. ローカルアプリを試験先に接続する場合はOpenAI・Resend・R2書込み/削除をモックにする。本番R2キーを共有した状態で授業削除やCronを実行しない。

pg_dump方式は空の専用復元先を用意し、その接続先を確認してから以下を実行し、同じ比較を行う:

```text
pg_restore --exit-on-error --single-transaction --no-owner --no-acl --dbname=<専用復元DB名> <バックアップファイル>
```

接続先ホスト等は子プロセス環境で指定する。本番・既存usage-testへ上書きしない。--cleanやdrop/createを自動で本番に実行しない。

これらは復元手順の試験であり、本番ブランチのインプレースRestore成功を実証するものではない。本番復旧では全書込みを停止し、検証済み復元先への接続切替、必要ならNeon対応の復旧機能を個別承認して行う。

## Alembic本番適用順序（承認後）

前提: 3者照合済み、書込み/削除ジョブ停止済み、バックアップと復元試験成功、固定コードの承認、Render Auto-Deployの現在値確認済み。環境設定やpushが勝手にデプロイを起動しないことを確認する。

1. 移行直前にREAD ONLYで既存8テーブルのスキーマとデータ基準を再取得する。最新の件数を使用し、以前の3ユーザー/15講義という値を固定の期待値にしない。追加usageテーブル・alembic_versionが予期せず存在すれば中止する。
2. 承認済みコード・本番環境設定の安全な実行環境で、書込み先を最終照合して実行する。

```text
python -m alembic upgrade 0001_baseline
python -m alembic current
python -m alembic upgrade 0002_usage_controls
python -m alembic current
```

3. 0001は既存8テーブルを検証し採用する。0002は追加7テーブルと制御行を作成する。既存レコードを削除・更新しない。不一致をstampで回避しない。各段階が失敗したら以降へ進まず、currentと実スキーマを確認する。
4. 全既存データが直前基準と一致すること、バージョン0002、stopped=true、pdf_ready=falseを確認する。再upgradeが台帳やデータを変更しないことも確認する。
5. 別途承認された本番DB変更としてusage-init-pdfsを実行する（R2はHEADのみ）。容量初期化完了を確認するまでPDFアップロードを再開しない。AI停止は維持する。
6. 承認された新版デプロイを完了してから、UI・保存済みPDF等を確認し、AI再開とCron開始を別途行う。Cron開始はR2削除を含むため個別承認が必要。

## 失敗時

DDL失敗時に自動downgradeやstampをしない。0001適用済み/0002未適用なら、原因修正後に0002から再開できるか調べる。DB破損がなければバックアップ復元はせず、追加テーブルと台帳を保持する。破損が確認された場合のみ検証済み復元先へ切り替える。復元時点後の利用額・データが失われ得るため、AI停止を維持して照合する。DB復元はR2物理削除を戻さない。

## 一次資料

- https://neon.com/docs/manage/branches
- https://github.com/neondatabase/website/blob/main/content/docs/manage/backup-pg-dump.md
- https://neon.com/docs/postgres/backup-restore/branch-restore

バックアップ作成・復元試験・本番移行は未実施。安全性を検証する計画を確定した段階であり、本番移行の実行条件は未達です。
