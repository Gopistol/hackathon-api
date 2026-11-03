"""MinIO(S3 호환) 파일 저장소. 서버의 MinIO 는 127.0.0.1:9000 에만 열려 있다.

브라우저가 MinIO 에 직접 닿지 못하므로 presigned URL 대신 API 가 파일을 받아 올리고,
내려받기도 API 를 거친다.
"""

import uuid
from functools import lru_cache

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from app.core.config import get_settings


class StorageDisabledError(RuntimeError):
    pass


@lru_cache
def _client() -> BaseClient:
    s = get_settings()
    if not s.s3_endpoint_url:
        raise StorageDisabledError("S3_ENDPOINT_URL 이 설정되지 않았습니다.")
    client = boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
    )
    try:
        client.head_bucket(Bucket=s.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=s.s3_bucket)
    return client


def upload(data: bytes, filename: str, content_type: str | None) -> str:
    key = f"{uuid.uuid4().hex}/{filename}"
    _client().put_object(
        Bucket=get_settings().s3_bucket,
        Key=key,
        Body=data,
        ContentType=content_type or "application/octet-stream",
    )
    return key


def download(key: str) -> tuple[bytes, str]:
    obj = _client().get_object(Bucket=get_settings().s3_bucket, Key=key)
    return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")
