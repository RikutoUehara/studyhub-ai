"""Private R2 object storage. Credentials never leave the server."""
import os
import boto3
from botocore.config import Config


class R2Storage:
    def __init__(self):
        names = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
        if any(not os.getenv(name) for name in names):
            raise RuntimeError("R2 configuration is incomplete")
        self.bucket = os.environ["R2_BUCKET_NAME"]
        self.client = boto3.client(
            "s3", endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto",
            config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30,
                          retries={"max_attempts": 2, "mode": "standard"}),
        )

    def put(self, key, data):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="application/pdf")

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)
