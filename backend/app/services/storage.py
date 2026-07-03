from app.firebase import get_bucket


def photo_path_prefix(uid: str) -> str:
    return f"users/{uid}/photos/"


def blob_exists(storage_path: str) -> bool:
    return get_bucket().blob(storage_path).exists()


def delete_blob(storage_path: str) -> None:
    blob = get_bucket().blob(storage_path)
    if blob.exists():
        blob.delete()
