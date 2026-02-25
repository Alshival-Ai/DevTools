from dataclasses import dataclass

import sqlite3
from pathlib import Path

from django.conf import settings

from .resources_store import _decrypt_key_text, _encrypt_key_text, _is_encrypted


@dataclass
class GlobalSSHCredentialItem:
    id: int
    name: str
    team_name: str
    created_at: str


def list_global_ssh_credentials() -> list[GlobalSSHCredentialItem]:
    _migrate_legacy_global_ssh_rows()
    conn = _connect_global_ssh_db()
    try:
        _ensure_global_ssh_schema(conn)
        rows = conn.execute(
            """
            SELECT id, name, COALESCE(team_name, '') AS team_name, created_at, encrypted_private_key
            FROM global_ssh_credentials
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY id DESC
            """
        ).fetchall()
        for row in rows:
            key_text = (row["encrypted_private_key"] or "").strip()
            if key_text and not _is_encrypted(key_text):
                conn.execute(
                    "UPDATE global_ssh_credentials SET encrypted_private_key = ?, updated_at = datetime('now') WHERE id = ?",
                    (_encrypt_key_text(key_text), int(row["id"])),
                )
        conn.commit()
        return [
            GlobalSSHCredentialItem(
                id=int(row["id"]),
                name=str(row["name"] or ""),
                team_name=str(row["team_name"] or ""),
                created_at=str(row["created_at"] or ""),
            )
            for row in rows
        ]
    finally:
        conn.close()


def add_global_ssh_credential(
    *,
    user,
    name: str,
    team_name: str,
    private_key_text: str,
) -> int:
    conn = _connect_global_ssh_db()
    try:
        _ensure_global_ssh_schema(conn)
        cursor = conn.execute(
            """
            INSERT INTO global_ssh_credentials (
                name, team_name, encrypted_private_key, created_by_user_id, updated_at
            ) VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (
                str(name or "").strip(),
                str(team_name or "").strip(),
                _encrypt_key_text(private_key_text),
                int(getattr(user, "id", 0) or 0),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def delete_global_ssh_credential(*, credential_id: int) -> None:
    conn = _connect_global_ssh_db()
    try:
        _ensure_global_ssh_schema(conn)
        conn.execute(
            "UPDATE global_ssh_credentials SET is_active = 0, updated_at = datetime('now') WHERE id = ?",
            (int(credential_id),),
        )
        conn.commit()
    finally:
        conn.close()


def get_global_ssh_private_key(*, credential_id: int) -> str | None:
    conn = _connect_global_ssh_db()
    try:
        _ensure_global_ssh_schema(conn)
        row = conn.execute(
            """
            SELECT encrypted_private_key
            FROM global_ssh_credentials
            WHERE id = ? AND COALESCE(is_active, 1) = 1
            """,
            (int(credential_id),),
        ).fetchone()
        if not row:
            return None
        decrypted = _decrypt_key_text((row["encrypted_private_key"] or "").strip())
        if not decrypted:
            return None
        return decrypted
    finally:
        conn.close()


def _global_ssh_db_path() -> Path:
    root = Path(getattr(settings, "GLOBAL_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "global_data"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "global.db"


def _connect_global_ssh_db() -> sqlite3.Connection:
    db_path = _global_ssh_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_global_ssh_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS global_ssh_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            team_name TEXT,
            encrypted_private_key TEXT NOT NULL,
            created_by_user_id INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_global_ssh_active_updated
        ON global_ssh_credentials(is_active, updated_at)
        """
    )
    conn.commit()


def _migrate_legacy_global_ssh_rows() -> None:
    try:
        from .models import GlobalTeamSSHCredential
    except Exception:
        return
    rows = list(
        GlobalTeamSSHCredential.objects.filter(is_active=True)
        .order_by("id")
        .values("name", "team_name", "encrypted_private_key", "created_by_id")
    )
    if not rows:
        return
    conn = _connect_global_ssh_db()
    try:
        _ensure_global_ssh_schema(conn)
        for row in rows:
            key_text = str(row.get("encrypted_private_key") or "").strip()
            if not key_text:
                continue
            encrypted_value = key_text if _is_encrypted(key_text) else _encrypt_key_text(key_text)
            exists = conn.execute(
                """
                SELECT id
                FROM global_ssh_credentials
                WHERE name = ? AND COALESCE(team_name, '') = ? AND encrypted_private_key = ? AND COALESCE(is_active, 1) = 1
                LIMIT 1
                """,
                (
                    str(row.get("name") or "").strip(),
                    str(row.get("team_name") or "").strip(),
                    encrypted_value,
                ),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                """
                INSERT INTO global_ssh_credentials (
                    name, team_name, encrypted_private_key, created_by_user_id, updated_at
                ) VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (
                    str(row.get("name") or "").strip(),
                    str(row.get("team_name") or "").strip(),
                    encrypted_value,
                    int(row.get("created_by_id") or 0),
                ),
            )
        conn.commit()
    finally:
        conn.close()
