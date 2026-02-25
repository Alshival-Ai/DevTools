import hashlib
import secrets


def generate_api_key(scope: str) -> str:
    normalized_scope = (scope or "account").strip().lower()
    if normalized_scope not in {"account", "team", "resource"}:
        normalized_scope = "account"
    token = secrets.token_urlsafe(32).rstrip("=")
    return f"alv_{normalized_scope}_{token}"


def hash_api_key(raw_api_key: str) -> str:
    return hashlib.sha256(str(raw_api_key or "").encode("utf-8")).hexdigest()


def key_prefix(raw_api_key: str, length: int = 24) -> str:
    value = str(raw_api_key or "").strip()
    if not value:
        return ""
    return value[: max(8, int(length))]


def key_preview(raw_api_key: str) -> str:
    value = str(raw_api_key or "").strip()
    if not value:
        return ""
    if len(value) <= 14:
        return value
    return f"{value[:10]}...{value[-4:]}"
