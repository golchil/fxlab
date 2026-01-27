"""MinIO client service for image storage."""
import io
from typing import Optional
from minio import Minio
from minio.error import S3Error

from app.config import settings


_client: Optional[Minio] = None


def get_minio_client() -> Minio:
    """Get or create MinIO client singleton."""
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def ensure_bucket_exists() -> None:
    """Create bucket if it doesn't exist."""
    client = get_minio_client()
    bucket = settings.minio_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def upload_image(image_key: str, data: bytes, content_type: str = "image/png") -> str:
    """
    Upload image to MinIO.

    Args:
        image_key: Object key (path) in the bucket
        data: Image data as bytes
        content_type: MIME type

    Returns:
        The image_key for reference
    """
    client = get_minio_client()
    ensure_bucket_exists()

    client.put_object(
        bucket_name=settings.minio_bucket,
        object_name=image_key,
        data=io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return image_key


def get_image(image_key: str) -> bytes:
    """
    Get image from MinIO.

    Args:
        image_key: Object key in the bucket

    Returns:
        Image data as bytes
    """
    client = get_minio_client()
    response = client.get_object(settings.minio_bucket, image_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def delete_image(image_key: str) -> None:
    """Delete image from MinIO."""
    client = get_minio_client()
    client.remove_object(settings.minio_bucket, image_key)


def image_exists(image_key: str) -> bool:
    """Check if image exists in MinIO."""
    client = get_minio_client()
    try:
        client.stat_object(settings.minio_bucket, image_key)
        return True
    except S3Error:
        return False
