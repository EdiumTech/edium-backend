import io
import os
import zipfile
from urllib.parse import quote

import boto3
from botocore.config import Config

from validation import DOCX_TYPE, MAX_FILE_SIZE, PDF_TYPE


class InvalidResume(Exception):
    pass


class ResumeStorage:
    def __init__(self):
        self.bucket = os.environ["RESUME_BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url="https://storage.yandexcloud.net",
            region_name="ru-central1",
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
        )

    def upload_form(self, *, object_key: str, upload_id: str, media_type: str, size: int) -> dict:
        return self.client.generate_presigned_post(
            Bucket=self.bucket,
            Key=object_key,
            Fields={"Content-Type": media_type, "x-amz-meta-upload-id": upload_id},
            Conditions=[
                {"Content-Type": media_type},
                {"x-amz-meta-upload-id": upload_id},
                ["content-length-range", size, size],
            ],
            ExpiresIn=600,
        )

    def validate_uploaded(self, *, object_key: str, upload_id: str, declared_type: str, declared_size: int) -> str:
        head = self.client.head_object(Bucket=self.bucket, Key=object_key)
        actual_size = int(head.get("ContentLength", 0))
        if actual_size != declared_size or not 0 < actual_size <= MAX_FILE_SIZE:
            raise InvalidResume("Размер загруженного файла не совпадает с заявленным.")
        metadata = {key.lower(): value for key, value in head.get("Metadata", {}).items()}
        if metadata.get("upload-id") != upload_id:
            raise InvalidResume("Файл не принадлежит этой сессии загрузки.")
        body = self.client.get_object(Bucket=self.bucket, Key=object_key)["Body"].read(MAX_FILE_SIZE + 1)
        if len(body) != actual_size:
            raise InvalidResume("Не удалось полностью проверить файл.")
        actual_type = detect_resume_type(body)
        if actual_type != declared_type:
            raise InvalidResume("Фактический тип файла не соответствует PDF или DOCX.")
        return actual_type

    def download_url(self, *, object_key: str, original_name: str) -> str:
        encoded_name = quote(original_name, safe="")
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": object_key,
                "ResponseContentDisposition": f"attachment; filename*=UTF-8''{encoded_name}",
                "ResponseContentType": "application/octet-stream",
            },
            ExpiresIn=60,
        )

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)


def detect_resume_type(content: bytes) -> str | None:
    if content.startswith(b"%PDF-") and b"%%EOF" in content[-4096:]:
        return PDF_TYPE
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" in names and "word/document.xml" in names:
                    document = archive.read("word/document.xml")
                    if document.lstrip().startswith(b"<?xml") or b"<w:document" in document[:2048]:
                        return DOCX_TYPE
        except (zipfile.BadZipFile, KeyError, RuntimeError, ValueError):
            return None
    return None
