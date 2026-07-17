"""Cloud storage via Cloudinary for document file uploads."""
import cloudinary
import cloudinary.uploader
import cloudinary.utils


def _get_config():
    from config import get_settings
    settings = get_settings()
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
    )


def upload_file(file_bytes: bytes, filename: str, doc_id: str, tenant_id: str) -> str:
    """Upload file to Cloudinary, return the public_id."""
    _get_config()
    public_id = f"{tenant_id}/{doc_id}/{filename}"
    result = cloudinary.uploader.upload(
        file_bytes,
        public_id=public_id,
        folder="meddocs/documents",
        resource_type="raw",
    )
    return result["public_id"]


def get_file_url(public_id: str) -> str:
    """Get a signed URL for the file."""
    _get_config()
    url, _ = cloudinary.utils.cloudinary_url(
        public_id,
        resource_type="raw",
        sign_url=True,
        secure=True,
    )
    return url


def delete_file(public_id: str) -> bool:
    """Delete file from Cloudinary."""
    _get_config()
    try:
        result = cloudinary.uploader.destroy(public_id, resource_type="raw")
        return result.get("result") == "ok"
    except Exception:
        return False
