from dataclasses import dataclass
import base64
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings

from .api_key_utils import generate_api_key, hash_api_key, key_prefix, key_preview


@dataclass
class GlobalTeamAPIKeyItem:
    id: int
    name: str
    team_name: str
    key_prefix: str
    created_at: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).isoformat()


def _iso_to_dt(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _global_api_db_path() -> Path:
    root = Path(getattr(settings, "GLOBAL_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "global_data"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "global.db"


def _connect_global_api_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_global_api_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_global_api_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS global_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            team_name TEXT,
            key_prefix TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            encrypted_key TEXT NOT NULL,
            expires_at TEXT,
            created_by_user_id INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_global_api_active_expiry
        ON global_api_keys(is_active, expires_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_global_api_hash
        ON global_api_keys(key_hash)
        """
    )
    conn.commit()


def _key_fernet() -> Fernet:
    keys = getattr(settings, "SSH_KEY_MASTER_KEYS", None) or []
    for raw_key in keys:
        try:
            return Fernet(raw_key)
        except Exception:
            continue
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    fallback = base64.urlsafe_b64encode(digest)
    return Fernet(fallback)


def _encrypt_api_key(raw_api_key: str) -> str:
    token = _key_fernet().encrypt(str(raw_api_key or "").encode("utf-8")).decode("utf-8")
    return f"enc:{token}"


def _migrate_legacy_global_api_keys() -> None:
    try:
        from .models import GlobalTeamAPIKey
    except Exception:
        return

    rows = list(
        GlobalTeamAPIKey.objects.order_by("id").values(
            "name",
            "team_name",
            "key_prefix",
            "key_hash",
            "encrypted_key",
            "expires_at",
            "created_by_id",
            "is_active",
            "created_at",
            "updated_at",
        )
    )
    if not rows:
        return

    conn = _connect_global_api_db()
    try:
        _ensure_global_api_schema(conn)
        for row in rows:
            resolved_hash = str(row.get("key_hash") or "").strip()
            if not resolved_hash:
                continue
            exists = conn.execute(
                """
                SELECT id
                FROM global_api_keys
                WHERE key_hash = ?
                LIMIT 1
                """,
                (resolved_hash,),
            ).fetchone()
            if exists:
                continue
            expires_at = row.get("expires_at")
            created_at = row.get("created_at")
            updated_at = row.get("updated_at")
            conn.execute(
                """
                INSERT INTO global_api_keys (
                    name, team_name, key_prefix, key_hash, encrypted_key, expires_at,
                    created_by_user_id, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row.get("name") or "").strip() or "Team API Key",
                    str(row.get("team_name") or "").strip(),
                    str(row.get("key_prefix") or "").strip(),
                    resolved_hash,
                    str(row.get("encrypted_key") or "").strip(),
                    _dt_to_iso(expires_at) if expires_at is not None else "",
                    int(row.get("created_by_id") or 0),
                    1 if bool(row.get("is_active", True)) else 0,
                    _dt_to_iso(created_at) if created_at is not None else _dt_to_iso(_utc_now()),
                    _dt_to_iso(updated_at) if updated_at is not None else _dt_to_iso(_utc_now()),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _expire_stale_global_keys(now: datetime | None = None) -> int:
    resolved_now = now or _utc_now()
    now_iso = _dt_to_iso(resolved_now)
    conn = _connect_global_api_db()
    try:
        _ensure_global_api_schema(conn)
        cursor = conn.execute(
            """
            UPDATE global_api_keys
            SET is_active = 0, updated_at = ?
            WHERE COALESCE(is_active, 1) = 1
              AND COALESCE(expires_at, '') <> ''
              AND expires_at <= ?
            """,
            (now_iso, now_iso),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def is_valid_global_team_api_key(raw_api_key: str, *, now=None) -> bool:
    _migrate_legacy_global_api_keys()
    resolved_key = str(raw_api_key or "").strip()
    if not resolved_key:
        return False
    resolved_now = now or _utc_now()
    _expire_stale_global_keys(resolved_now)
    key_hash_value = hash_api_key(resolved_key)

    conn = _connect_global_api_db()
    try:
        _ensure_global_api_schema(conn)
        row = conn.execute(
            """
            SELECT id, expires_at
            FROM global_api_keys
            WHERE COALESCE(is_active, 1) = 1
              AND key_hash = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (key_hash_value,),
        ).fetchone()
        if not row:
            return False
        expires_at = _iso_to_dt(row["expires_at"])
        if expires_at is None:
            return True
        return expires_at > resolved_now
    finally:
        conn.close()


def list_global_team_api_keys() -> list[GlobalTeamAPIKeyItem]:
    _migrate_legacy_global_api_keys()
    now = _utc_now()
    _expire_stale_global_keys(now)
    now_iso = _dt_to_iso(now)
    conn = _connect_global_api_db()
    try:
        _ensure_global_api_schema(conn)
        rows = conn.execute(
            """
            SELECT id, name, team_name, key_prefix, created_at
            FROM global_api_keys
            WHERE COALESCE(is_active, 1) = 1
              AND (COALESCE(expires_at, '') = '' OR expires_at > ?)
            ORDER BY id DESC
            """,
            (now_iso,),
        ).fetchall()
        return [
            GlobalTeamAPIKeyItem(
                id=int(row["id"]),
                name=str(row["name"] or ""),
                team_name=str(row["team_name"] or ""),
                key_prefix=key_preview(str(row["key_prefix"] or "")),
                created_at=str(row["created_at"] or ""),
            )
            for row in rows
        ]
    finally:
        conn.close()


def create_global_team_api_key(*, user, name: str, team_name: str = "") -> tuple[int, str]:
    _migrate_legacy_global_api_keys()
    now = _utc_now()
    _expire_stale_global_keys(now)
    raw_key = generate_api_key("team")
    now_iso = _dt_to_iso(now)
    expires_iso = _dt_to_iso(now + timedelta(hours=1))

    conn = _connect_global_api_db()
    try:
        _ensure_global_api_schema(conn)
        conn.execute(
            """
            UPDATE global_api_keys
            SET expires_at = ?, updated_at = ?
            WHERE COALESCE(is_active, 1) = 1
              AND (COALESCE(expires_at, '') = '' OR expires_at > ?)
            """,
            (expires_iso, now_iso, now_iso),
        )
        cursor = conn.execute(
            """
            INSERT INTO global_api_keys (
                name, team_name, key_prefix, key_hash, encrypted_key, expires_at,
                created_by_user_id, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                (name or "Team API Key").strip() or "Team API Key",
                (team_name or "").strip(),
                key_prefix(raw_key),
                hash_api_key(raw_key),
                _encrypt_api_key(raw_key),
                "",
                int(getattr(user, "id", 0) or 0),
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid), raw_key
    finally:
        conn.close()
