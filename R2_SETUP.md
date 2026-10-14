# PDFのR2保存

PDF本体は非公開R2バケットに保存し、`lecture.pdf_filename` にはオブジェクトキーを保存します。
所有者は `lecture.course.user_id` で判定します。UUID付きキーだけではアクセス制御になりません。
PDF取得はログインと所有者チェックを通る `/lecture/<id>/pdf` を使います。
`?download=1` でダウンロードします。旧 `/uploads/` は404になります。

## 設定と起動

`.env.example` を参考にR2の4変数を設定し、バケットの公開アクセスは無効のままにします。
APIキーはサーバーの環境変数で管理し、Gitやブラウザへ渡しません。

```powershell
venv/Scripts/python.exe -m pip install -r requirements.txt
venv/Scripts/python.exe -m flask --app app init-db
venv/Scripts/python.exe app.py
```

追加テーブルは `pdf_deletion` です。既存列の変更はありません。
旧ローカルPDFは自動移行しません。残っている場合は別途移行が必要です。
デフォルト上限はPDF本体10 MiB、200ページ、フォームを含むリクエスト11 MiBです。
暗号化・壊れたPDFを拒否します。画像だけのPDFは保存できますがOCRは行いません。
差し替え時は以前の要約・チャット・テスト結果を削除します。

## DBとR2の整合性

アップロード前に新しいキーを `pdf_deletion` へ記録します。
R2保存とDB更新が成功したら、そのキーの削除待ちを解除します。
DB保存失敗やプロセス停止で残るオブジェクトは、1時間後から回収できます。
差し替え・授業削除では古いキーをDBトランザクション内で削除待ちへ登録し、
コミット後にR2から削除します。削除失敗はログに記録し、ジョブを残します。
DBがまだ参照しているキーは削除しません。

本番では以下を定期実行してください（例えば5分ごと）。
自動定期実行の設定自体は、デプロイ先で行います。

```powershell
venv/Scripts/python.exe -m flask --app app cleanup-pdfs
```

DBだけをSQLで一括削除すると、この削除待ち登録は行われません。
R2のファイルも削除する必要があります。`pdf_deletion` の未処理行を不用意に削除しないでください。
PDFはFlaskを通して配信するため、転送中はアプリの処理資源を使用します。
本番のワーカー数・タイムアウト設定を確認してください。

## 検証

```powershell
venv/Scripts/python.exe -m unittest discover -s tests -v
```

テストはメモリDBと偽のストレージを使います。実際のNeon・R2を書き換えません。
実環境ではPDFアップロード、抽出文字、再読み込み、アプリ再起動後の閲覧、
別ユーザーのアクセス拒否、差し替え、授業削除をブラウザで確認してください。
