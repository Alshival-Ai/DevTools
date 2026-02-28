from __future__ import annotations

import json
import os
import re
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model

from .models import UserFeatureAccess, UserNotificationSettings
from .resources_store import _global_owner_dir


_USER_COLLECTION_NAME = "user_records"
_USER_EMBEDDING_DIM = 384


def _stable_json_hash(value: object) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except Exception:
        payload = str(value or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_runtime_cache_dirs() -> None:
    base_dir = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parent.parent))
    candidates = []
    current = str(os.getenv("XDG_CACHE_HOME") or "").strip()
    if current:
        candidates.append(Path(current))
    candidates.append(base_dir / "var" / "cache")
    candidates.append(Path("/tmp/alshival-cache"))

    for cache_root in candidates:
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
            probe = cache_root / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception:
            continue
        os.environ["XDG_CACHE_HOME"] = str(cache_root)
        os.environ["CHROMA_CACHE_DIR"] = str(cache_root / "chroma")
        os.environ.setdefault("HF_HOME", str(cache_root / "huggingface"))
        current_home = str(os.getenv("HOME") or "").strip()
        if not current_home or current_home == "/":
            home_dir = cache_root / "home"
            try:
                home_dir.mkdir(parents=True, exist_ok=True)
                os.environ["HOME"] = str(home_dir)
            except Exception:
                pass
        return


def _normalize_phone(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    keep_plus = raw.startswith("+")
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""
    return f"+{digits}" if keep_plus else digits


def _deterministic_embedding(text: str, dim: int = _USER_EMBEDDING_DIM) -> list[float]:
    seed = hashlib.sha256(str(text or "").encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < dim:
        seed = hashlib.sha256(seed).digest()
        for byte in seed:
            values.append((byte / 127.5) - 1.0)
            if len(values) >= dim:
                break
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _chroma_collection():
    _ensure_runtime_cache_dirs()
    try:
        import chromadb
    except Exception:
        return None
    knowledge_path = _global_owner_dir() / "knowledge.db"
    try:
        client = chromadb.PersistentClient(path=str(knowledge_path))
        return client.get_or_create_collection(name=_USER_COLLECTION_NAME)
    except Exception:
        return None


def _user_record_id(user_id: int) -> str:
    resolved = int(user_id or 0)
    if resolved <= 0:
        return ""
    return f"user:{resolved}"


def _collection_metadata_value(collection, *, record_id: str, key: str) -> str:
    if collection is None or not record_id or not key:
        return ""
    try:
        payload = collection.get(ids=[record_id], include=["metadatas"])
    except Exception:
        return ""
    metadatas = payload.get("metadatas") if isinstance(payload, dict) else None
    if not isinstance(metadatas, list) or not metadatas:
        return ""
    first = metadatas[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get(key) or "").strip()


def _user_phone_value(user) -> str:
    user_id = int(getattr(user, "id", 0) or 0)
    if user_id <= 0:
        return ""
    row = UserNotificationSettings.objects.filter(user_id=user_id).first()
    if row is None:
        return ""
    return _normalize_phone(str(getattr(row, "phone_number", "") or ""))


def _build_user_record_payload(user) -> tuple[str, dict[str, Any], str, str]:
    user_id = int(getattr(user, "id", 0) or 0)
    username = str(getattr(user, "username", "") or "").strip()
    email = str(getattr(user, "email", "") or "").strip().lower()
    first_name = str(getattr(user, "first_name", "") or "").strip()
    last_name = str(getattr(user, "last_name", "") or "").strip()
    full_name = " ".join([part for part in [first_name, last_name] if part]).strip()
    phone = _user_phone_value(user)
    phone_digits = phone.lstrip("+") if phone.startswith("+") else phone
    is_active = bool(getattr(user, "is_active", False))
    is_staff = bool(getattr(user, "is_staff", False))
    is_superuser = bool(getattr(user, "is_superuser", False))
    team_names = list(user.groups.order_by("name").values_list("name", flat=True))
    feature_keys = list(
        UserFeatureAccess.objects.filter(user_id=user_id, is_enabled=True)
        .order_by("feature_key")
        .values_list("feature_key", flat=True)
    )
    updated_at = datetime.now(timezone.utc).isoformat()
    user_payload = {
        "id": user_id,
        "username": username,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "phone_number": phone,
        "is_active": is_active,
        "is_staff": is_staff,
        "is_superuser": is_superuser,
        "team_names": team_names,
        "feature_keys": feature_keys,
    }
    context_hash = _stable_json_hash(user_payload)
    document_json = {"user": user_payload, "updated_at": updated_at}
    document = " | ".join(
        [
            username,
            email,
            full_name,
            phone,
            phone_digits,
            "superuser" if is_superuser else "",
            "staff" if is_staff else "",
            "active" if is_active else "inactive",
            "teams",
            " ".join(team_names),
            "features",
            " ".join(feature_keys),
        ]
    ).strip(" |")
    metadata = {
        "source": "user_record",
        "collection_name": _USER_COLLECTION_NAME,
        "owner_scope": "global",
        "owner_user_id": user_id,
        "owner_team_id": 0,
        "resource_uuid": "",
        "user_id": user_id,
        "username": username,
        "email": email,
        "full_name": full_name,
        "phone_number": phone,
        "phone_digits": phone_digits,
        "is_active": is_active,
        "is_staff": is_staff,
        "is_superuser": is_superuser,
        "team_names": ",".join(team_names),
        "feature_keys": ",".join(feature_keys),
        "user_context_hash": context_hash,
        "updated_at": updated_at,
        "user_document_json": json.dumps(document_json, separators=(",", ":"), ensure_ascii=False),
    }
    return document, metadata, updated_at, context_hash


def upsert_user_record(user) -> bool:
    user_id = int(getattr(user, "id", 0) or 0)
    record_id = _user_record_id(user_id)
    if not record_id:
        return False
    if not bool(getattr(user, "is_active", False)):
        return delete_user_record(user_id)

    collection = _chroma_collection()
    if collection is None:
        return False
    try:
        document, metadata, _updated_at, context_hash = _build_user_record_payload(user)
        existing_context_hash = _collection_metadata_value(
            collection,
            record_id=record_id,
            key="user_context_hash",
        )
        if existing_context_hash and existing_context_hash == context_hash:
            return True
        resolved_document = document or username_for_fallback(user)
        collection.upsert(
            ids=[record_id],
            documents=[resolved_document],
            metadatas=[metadata],
            embeddings=[_deterministic_embedding(resolved_document)],
        )
        return True
    except Exception:
        return False


def username_for_fallback(user) -> str:
    username = str(getattr(user, "username", "") or "").strip()
    if username:
        return username
    user_id = int(getattr(user, "id", 0) or 0)
    if user_id > 0:
        return f"user_{user_id}"
    return "user_record"


def delete_user_record(user_id: int) -> bool:
    record_id = _user_record_id(user_id)
    if not record_id:
        return False
    collection = _chroma_collection()
    if collection is None:
        return False
    try:
        collection.delete(ids=[record_id])
        return True
    except Exception:
        return False


def sync_all_user_records() -> dict[str, int]:
    User = get_user_model()
    total = 0
    upserted = 0
    deleted = 0
    for user in User.objects.order_by("id"):
        total += 1
        if bool(getattr(user, "is_active", False)):
            if upsert_user_record(user):
                upserted += 1
        else:
            if delete_user_record(int(getattr(user, "id", 0) or 0)):
                deleted += 1
    return {"total": int(total), "upserted": int(upserted), "deleted": int(deleted)}


def get_user_record_by_user_id(user_id: int) -> tuple[dict[str, Any], str]:
    resolved_user_id = int(user_id or 0)
    record_id = _user_record_id(resolved_user_id)
    if not record_id:
        return {}, "user_id is required"

    collection = _chroma_collection()
    if collection is None:
        return {}, "user_records collection is unavailable"

    try:
        payload = collection.get(ids=[record_id])
        ids = payload.get("ids") or []
        docs = payload.get("documents") or []
        metas = payload.get("metadatas") or []
        if not ids:
            return {}, "user record not found"

        item_id = ids[0]
        if isinstance(item_id, list):
            item_id = item_id[0] if item_id else ""
        item_doc = docs[0] if docs else ""
        if isinstance(item_doc, list):
            item_doc = item_doc[0] if item_doc else ""
        item_meta = metas[0] if metas else {}
        if isinstance(item_meta, list):
            item_meta = item_meta[0] if item_meta else {}
        if not isinstance(item_meta, dict):
            item_meta = {}

        row: dict[str, Any] = {
            "id": str(item_id or ""),
            "document": str(item_doc or ""),
            "metadata": item_meta,
            "distance": None,
        }
        raw_document_json = str(item_meta.get("user_document_json") or "").strip()
        if raw_document_json:
            try:
                parsed_document = json.loads(raw_document_json)
            except Exception:
                parsed_document = {}
            if isinstance(parsed_document, dict):
                row["user_document"] = parsed_document
        return row, ""
    except Exception as exc:
        return {}, f"user record lookup failed: {exc}"


def query_user_records(*, query: str = "", phone: str = "", limit: int = 10) -> tuple[list[dict[str, Any]], str]:
    collection = _chroma_collection()
    if collection is None:
        return [], "user_records collection is unavailable"

    resolved_limit = max(1, min(int(limit or 10), 100))
    resolved_query = str(query or "").strip()
    normalized_phone = _normalize_phone(phone)
    phone_digits = normalized_phone.lstrip("+") if normalized_phone.startswith("+") else normalized_phone

    where_filter: dict[str, Any] | None = None
    if normalized_phone:
        if phone_digits and phone_digits != normalized_phone:
            where_filter = {
                "$or": [
                    {"phone_number": normalized_phone},
                    {"phone_number": phone_digits},
                    {"phone_digits": phone_digits},
                ]
            }
        else:
            where_filter = {"$or": [{"phone_number": normalized_phone}, {"phone_digits": phone_digits}]}

    rows: list[dict[str, Any]] = []
    try:
        if resolved_query:
            payload = collection.query(
                query_embeddings=[_deterministic_embedding(resolved_query)],
                n_results=resolved_limit,
                where=where_filter,
            )
            ids = (payload.get("ids") or [[]])[0]
            docs = (payload.get("documents") or [[]])[0]
            metas = (payload.get("metadatas") or [[]])[0]
            dists = (payload.get("distances") or [[]])[0]
            for idx, item_id in enumerate(ids):
                rows.append(
                    {
                        "id": str(item_id or ""),
                        "document": str(docs[idx] or "") if idx < len(docs) else "",
                        "metadata": metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {},
                        "distance": dists[idx] if idx < len(dists) else None,
                    }
                )
            return rows, ""

        payload = collection.get(where=where_filter, limit=resolved_limit)
        ids = payload.get("ids") or []
        docs = payload.get("documents") or []
        metas = payload.get("metadatas") or []
        for idx, item_id in enumerate(ids):
            rows.append(
                {
                    "id": str(item_id or ""),
                    "document": str(docs[idx] or "") if idx < len(docs) else "",
                    "metadata": metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {},
                    "distance": None,
                }
            )
        return rows, ""
    except Exception as exc:
        return [], f"user_records query failed: {exc}"
