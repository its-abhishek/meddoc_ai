"""Cloud storage via Cloudinary for private document file uploads."""
import os
import re
import time

import cloudinary
import cloudinary.uploader
import cloudinary.utils


class CloudStorageConfigurationError(RuntimeError):
    """Raised when the Cloudinary credentials have not been configured."""


def _get_config():
    from config import get_settings

    settings = get_settings()
    missing = [
        name for name, value in {
            "CLOUDINARY_CLOUD_NAME": settings.CLOUDINARY_CLOUD_NAME,
            "CLOUDINARY_API_KEY": settings.CLOUDINARY_API_KEY,
            "CLOUDINARY_API_SECRET": settings.CLOUDINARY_API_SECRET,
        }.items() if not value
    ]
    if missing:
        raise CloudStorageConfigurationError(
            f"Cloudinary is not configured; missing {', '.join(missing)}"
        )

    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def _safe_filename(filename: str) -> str:
    """Return a filesystem- and URL-safe name while retaining its extension."""
    name = os.path.basename(filename or "document")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or "document"


def upload_file(file_bytes: bytes, filename: str, doc_id: str, tenant_id: str) -> str:
    """Upload file to Cloudinary as authenticated (private), return the public_id."""
    _get_config()
    # Raw Cloudinary assets require the extension in their public ID. Keep the
    # original display name in the database, but never use untrusted path text.
    public_id = f"meddocs/documents/{tenant_id}/{doc_id}/{_safe_filename(filename)}"
    result = cloudinary.uploader.upload(
        file_bytes,
        public_id=public_id,
        resource_type="raw",
        type="authenticated",
        overwrite=False,
    )
    return result["public_id"]


def get_file_url(public_id: str, expiry: int = 3600) -> str:
    """Get a signed URL with expiry (default 1 hour)."""
    _get_config()
    _, extension = os.path.splitext(public_id)
    if not extension:
        raise ValueError("Cloudinary raw asset public_id must include a file extension")
    return cloudinary.utils.private_download_url(
        public_id,
        extension.lstrip("."),
        resource_type="raw",
        type="authenticated",
        expires_at=int(time.time()) + expiry,
    )


def delete_file(public_id: str) -> bool:
    """Delete file from Cloudinary."""
    _get_config()
    try:
        result = cloudinary.uploader.destroy(public_id, resource_type="raw", type="authenticated")
        return result.get("result") == "ok"
    except Exception:
        return False
