"""Where generated files go: a private S3 bucket (deployed) or a local folder
(local development). Either way the caller gets back a URL to download each
file from.

  STORAGE_MODE=s3     RESULTS_BUCKET=<bucket>   URL_TTL_SECONDS=3600
  STORAGE_MODE=local  LOCAL_OUTPUT_DIR=<dir>    (served at /output/ by dev/local_server.py)
"""

import mimetypes
import os
import re
import shutil

CONTENT_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".zip": "application/zip",
}


def safe_download_name(name):
    """File name that is safe to put in a Content-Disposition header. Output
    names can include text from the uploaded log (e.g. its last date), so
    keep only characters that can't break out of the quoted header value."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "download"


def content_type(name):
    ext = os.path.splitext(name)[1].lower()
    return CONTENT_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


class S3Storage:
    def __init__(self, bucket, ttl_seconds=3600):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.ttl = ttl_seconds
        self.s3 = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION"),
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
        )

    def save(self, local_path, key):
        name = safe_download_name(os.path.basename(key))
        with open(local_path, "rb") as f:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=f,
                ContentType=content_type(name),
                ContentDisposition=f'attachment; filename="{name}"',
            )
        return self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.ttl
        )


class LocalStorage:
    def __init__(self, root, url_prefix="/output/"):
        self.root = root
        self.url_prefix = url_prefix

    def save(self, local_path, key):
        dest = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(local_path, dest)
        return self.url_prefix + key


def from_environment():
    if os.environ.get("STORAGE_MODE", "s3") == "local":
        return LocalStorage(os.environ.get("LOCAL_OUTPUT_DIR", os.path.abspath("local-output")))
    return S3Storage(os.environ["RESULTS_BUCKET"], int(os.environ.get("URL_TTL_SECONDS", "3600")))
