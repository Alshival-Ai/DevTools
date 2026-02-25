from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from .models import ResourcePackageOwner
from .resources_store import _user_owner_dir, get_resource_knowledge_db_path, get_resource_owner_context, list_resource_notes


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


def _connect_sqlite(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _owner_collection_name(owner_scope: str) -> str:
    scope = str(owner_scope or "").strip().lower()
    if scope == "global":
        return "global_resources"
    if scope == "team":
        return "team_resources"
    return "user_resources"


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return "{}"


def _owner_sqlite_db_path(owner_root: Path, owner_scope: str) -> Path:
    scope = str(owner_scope or "").strip().lower()
    if scope == "global":
        return owner_root / "global.db"
    if scope == "team":
        return owner_root / "team.db"
    return owner_root / "member.db"


def _ensure_owner_snapshot_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_health_snapshots (
            resource_uuid TEXT PRIMARY KEY,
            collection_name TEXT NOT NULL,
            name TEXT NOT NULL,
            owner_scope TEXT NOT NULL,
            owner_user_id INTEGER NOT NULL DEFAULT 0,
            owner_team_id INTEGER NOT NULL DEFAULT 0,
            status TEXT,
            checked_at TEXT,
            check_method TEXT,
            latency_ms REAL,
            packet_loss_pct REAL,
            error TEXT,
            target TEXT,
            address TEXT,
            port TEXT,
            healthcheck_url TEXT,
            resource_type TEXT,
            ssh_configured INTEGER NOT NULL DEFAULT 0,
            resource_metadata TEXT NOT NULL DEFAULT '{}',
            document_json TEXT NOT NULL DEFAULT '{}',
            document_text TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_res_health_snap_updated
        ON resource_health_snapshots(updated_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_res_health_snap_collection
        ON resource_health_snapshots(collection_name, updated_at)
        """
    )
    conn.commit()


def _ensure_chroma_path(path: Path) -> Path:
    if path.exists() and path.is_file():
        backup = path.with_name(f"{path.name}.sqlite_legacy")
        if not backup.exists():
            path.rename(backup)
        else:
            path.unlink(missing_ok=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_chroma_collection(knowledge_path: Path):
    _ensure_runtime_cache_dirs()
    try:
        import chromadb
    except Exception:
        return None
    resolved_path = _ensure_chroma_path(knowledge_path)
    client = chromadb.PersistentClient(path=str(resolved_path))
    return client.get_or_create_collection(name="resources")


def _build_chroma_metadata(
    *,
    resource_uuid: str,
    owner_scope: str,
    owner_user_id: int,
    owner_team_id: int,
    resource,
    status: str,
    checked_at: str,
    check_method: str,
    latency_ms: float | None,
    packet_loss_pct: float | None,
    ssh_configured: bool,
    document_json: str,
) -> dict[str, Any]:
    return {
        "resource_uuid": resource_uuid,
        "collection_name": "resources",
        "owner_scope": owner_scope,
        "owner_user_id": owner_user_id,
        "owner_team_id": owner_team_id,
        "status": str(status or "").strip(),
        "check_method": str(check_method or "").strip(),
        "latency_ms": float(latency_ms) if latency_ms is not None else -1.0,
        "packet_loss_pct": float(packet_loss_pct) if packet_loss_pct is not None else -1.0,
        "checked_at": str(checked_at or "").strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "name": str(getattr(resource, "name", "") or "").strip(),
        "resource_type": str(getattr(resource, "resource_type", "") or "").strip(),
        "target": str(getattr(resource, "target", "") or "").strip(),
        "ssh_configured": bool(ssh_configured),
        "resource_document_json": document_json,
    }


def _build_document(resource, owner_context: dict[str, Any], check_payload: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
    resource_uuid = str(getattr(resource, "resource_uuid", "") or "").strip()
    notes_rows = list_resource_notes(owner_context.get("owner_user"), resource_uuid, limit=200)
    notes_payload: list[dict[str, Any]] = []
    for row in notes_rows:
        notes_payload.append(
            {
                "id": int(getattr(row, "id", 0) or 0),
                "body": str(getattr(row, "body", "") or ""),
                "author_user_id": int(getattr(row, "author_user_id", 0) or 0),
                "author_username": str(getattr(row, "author_username", "") or ""),
                "created_at": str(getattr(row, "created_at", "") or ""),
                "attachment_id": int(getattr(row, "attachment_id", 0) or 0) if getattr(row, "attachment_id", None) else None,
                "attachment_name": str(getattr(row, "attachment_name", "") or ""),
                "attachment_content_type": str(getattr(row, "attachment_content_type", "") or ""),
                "attachment_size": int(getattr(row, "attachment_size", 0) or 0),
            }
        )

    ssh_username = str(getattr(resource, "ssh_username", "") or "").strip()
    ssh_key_name = str(getattr(resource, "ssh_key_name", "") or "").strip()
    ssh_credential_id = str(getattr(resource, "ssh_credential_id", "") or "").strip()
    ssh_configured = bool(
        ssh_username and (bool(getattr(resource, "ssh_key_present", False)) or ssh_key_name or ssh_credential_id)
    )

    document: dict[str, Any] = {
        "resource": {
            "id": int(getattr(resource, "id", 0) or 0),
            "resource_uuid": resource_uuid,
            "name": str(getattr(resource, "name", "") or "").strip() or resource_uuid,
            "access_scope": str(getattr(resource, "access_scope", "") or "account").strip() or "account",
            "team_names": list(getattr(resource, "team_names", []) or []),
            "resource_type": str(getattr(resource, "resource_type", "") or "unknown").strip() or "unknown",
            "target": str(getattr(resource, "target", "") or "").strip(),
            "address": str(getattr(resource, "address", "") or "").strip(),
            "port": str(getattr(resource, "port", "") or "").strip(),
            "db_type": str(getattr(resource, "db_type", "") or "").strip(),
            "healthcheck_url": str(getattr(resource, "healthcheck_url", "") or "").strip(),
            "notes": str(getattr(resource, "notes", "") or ""),
            "created_at": str(getattr(resource, "created_at", "") or ""),
            "last_status": str(getattr(resource, "last_status", "") or ""),
            "last_checked_at": str(getattr(resource, "last_checked_at", "") or ""),
            "last_error": str(getattr(resource, "last_error", "") or ""),
            "ssh_key_name": ssh_key_name,
            "ssh_username": ssh_username,
            "ssh_key_present": bool(getattr(resource, "ssh_key_present", False)),
            "ssh_port": str(getattr(resource, "ssh_port", "") or ""),
            "ssh_credential_id": ssh_credential_id,
            "ssh_credential_scope": str(getattr(resource, "ssh_credential_scope", "") or "").strip(),
            "ssh_configured": ssh_configured,
            "resource_subtype": str(getattr(resource, "resource_subtype", "") or "").strip(),
            "resource_metadata": getattr(resource, "resource_metadata", {}) or {},
        },
        "owner_context": {
            "owner_scope": str(owner_context.get("owner_scope") or "user"),
            "owner_user_id": int(owner_context.get("owner_user_id") or 0),
            "owner_team_id": int(owner_context.get("owner_team_id") or 0),
        },
        "notes_thread": notes_payload,
        "latest_health": {
            "status": str(check_payload.get("status") or ""),
            "checked_at": str(check_payload.get("checked_at") or ""),
            "error": str(check_payload.get("error") or ""),
            "check_method": str(check_payload.get("check_method") or ""),
            "latency_ms": check_payload.get("latency_ms"),
            "packet_loss_pct": check_payload.get("packet_loss_pct"),
        },
    }

    doc_text_parts = [
        str(document["resource"]["name"] or ""),
        str(document["resource"]["resource_type"] or ""),
        str(document["resource"]["target"] or ""),
        str(document["resource"]["address"] or ""),
        str(document["resource"]["db_type"] or ""),
        str(document["resource"]["notes"] or ""),
        str(document["latest_health"]["status"] or ""),
        str(document["latest_health"]["error"] or ""),
    ]
    for note in notes_payload:
        note_body = str(note.get("body") or "").strip()
        if note_body:
            doc_text_parts.append(note_body)
    document_text = " | ".join(part for part in doc_text_parts if str(part).strip())
    return document, document_text, ssh_configured


def _upsert_owner_snapshot(
    *,
    owner_root: Path,
    owner_scope: str,
    owner_user_id: int,
    owner_team_id: int,
    resource,
    collection_name: str,
    status: str,
    checked_at: str,
    error: str,
    check_method: str,
    latency_ms: float | None,
    packet_loss_pct: float | None,
    document_json: str,
    document_text: str,
    ssh_configured: bool,
) -> None:
    db_path = _owner_sqlite_db_path(owner_root, owner_scope)
    conn = _connect_sqlite(db_path)
    try:
        _ensure_owner_snapshot_schema(conn)
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO resource_health_snapshots (
                resource_uuid, collection_name, name, owner_scope, owner_user_id, owner_team_id,
                status, checked_at, check_method, latency_ms, packet_loss_pct, error,
                target, address, port, healthcheck_url, resource_type, ssh_configured,
                resource_metadata, document_json, document_text, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource_uuid) DO UPDATE SET
                collection_name=excluded.collection_name,
                name=excluded.name,
                owner_scope=excluded.owner_scope,
                owner_user_id=excluded.owner_user_id,
                owner_team_id=excluded.owner_team_id,
                status=excluded.status,
                checked_at=excluded.checked_at,
                check_method=excluded.check_method,
                latency_ms=excluded.latency_ms,
                packet_loss_pct=excluded.packet_loss_pct,
                error=excluded.error,
                target=excluded.target,
                address=excluded.address,
                port=excluded.port,
                healthcheck_url=excluded.healthcheck_url,
                resource_type=excluded.resource_type,
                ssh_configured=excluded.ssh_configured,
                resource_metadata=excluded.resource_metadata,
                document_json=excluded.document_json,
                document_text=excluded.document_text,
                updated_at=excluded.updated_at
            """,
            (
                str(getattr(resource, "resource_uuid", "") or "").strip(),
                collection_name,
                str(getattr(resource, "name", "") or "").strip() or str(getattr(resource, "resource_uuid", "") or ""),
                owner_scope,
                int(owner_user_id or 0),
                int(owner_team_id or 0),
                str(status or "").strip(),
                str(checked_at or "").strip(),
                str(check_method or "").strip(),
                latency_ms,
                packet_loss_pct,
                str(error or "").strip(),
                str(getattr(resource, "target", "") or "").strip(),
                str(getattr(resource, "address", "") or "").strip(),
                str(getattr(resource, "port", "") or "").strip(),
                str(getattr(resource, "healthcheck_url", "") or "").strip(),
                str(getattr(resource, "resource_type", "") or "").strip(),
                1 if ssh_configured else 0,
                _safe_json_dumps(getattr(resource, "resource_metadata", {}) or {}),
                document_json,
                document_text,
                now_iso,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_resource_health_knowledge(
    *,
    user,
    resource,
    status: str,
    checked_at: str,
    error: str,
    check_method: str,
    latency_ms: float | None,
    packet_loss_pct: float | None,
) -> None:
    resource_uuid = str(getattr(resource, "resource_uuid", "") or "").strip()
    if not resource_uuid:
        return

    knowledge_db_path = get_resource_knowledge_db_path(user, resource_uuid)
    owner_context: dict[str, Any] = get_resource_owner_context(user, resource_uuid)
    owner_scope = str(owner_context.get("owner_scope") or "user")
    collection_name = _owner_collection_name(owner_scope)
    owner_user_id = int(owner_context.get("owner_user_id") or 0)
    owner_team_id = int(owner_context.get("owner_team_id") or 0)
    owner_root = Path(owner_context.get("owner_root") or knowledge_db_path.parent)
    owner_user = user
    if owner_user_id > 0:
        try:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            resolved_user = User.objects.filter(id=owner_user_id).first()
            if resolved_user is not None:
                owner_user = resolved_user
        except Exception:
            pass

    document, document_text, ssh_configured = _build_document(
        resource,
        {
            "owner_scope": owner_scope,
            "owner_user_id": owner_user_id,
            "owner_team_id": owner_team_id,
            "owner_user": owner_user,
        },
        {
            "status": status,
            "checked_at": checked_at,
            "error": error,
            "check_method": check_method,
            "latency_ms": latency_ms,
            "packet_loss_pct": packet_loss_pct,
        },
    )
    document_json = _safe_json_dumps(document)

    metadata = _build_chroma_metadata(
        resource_uuid=resource_uuid,
        owner_scope=owner_scope,
        owner_user_id=owner_user_id,
        owner_team_id=owner_team_id,
        resource=resource,
        status=status,
        checked_at=checked_at,
        check_method=check_method,
        latency_ms=latency_ms,
        packet_loss_pct=packet_loss_pct,
        ssh_configured=ssh_configured,
        document_json=document_json,
    )
    if owner_scope == "team" and owner_team_id > 0:
        team = Group.objects.filter(id=owner_team_id).first()
        team_members = list(team.user_set.filter(is_active=True).order_by("id")) if team is not None else []
        for member in team_members:
            member_kb_path = _user_owner_dir(member) / "knowledge.db"
            member_collection = _get_chroma_collection(member_kb_path)
            if member_collection is None:
                continue
            member_collection.upsert(
                ids=[resource_uuid],
                documents=[document_text or str(getattr(resource, "name", "") or resource_uuid)],
                metadatas=[metadata],
            )
    else:
        collection = _get_chroma_collection(knowledge_db_path)
        if collection is not None:
            collection.upsert(
                ids=[resource_uuid],
                documents=[document_text or str(getattr(resource, "name", "") or resource_uuid)],
                metadatas=[metadata],
            )

    _upsert_owner_snapshot(
        owner_root=owner_root,
        owner_scope=owner_scope,
        owner_user_id=owner_user_id,
        owner_team_id=owner_team_id,
        resource=resource,
        collection_name=collection_name,
        status=status,
        checked_at=checked_at,
        error=error,
        check_method=check_method,
        latency_ms=latency_ms,
        packet_loss_pct=packet_loss_pct,
        document_json=document_json,
        document_text=document_text,
        ssh_configured=ssh_configured,
    )


def _iter_owner_roots() -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    user_root = Path(getattr(settings, "USER_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "user_data"))
    team_root = Path(getattr(settings, "TEAM_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "team_data"))
    global_root = Path(getattr(settings, "GLOBAL_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "global_data"))

    if user_root.exists():
        for entry in user_root.iterdir():
            if entry.is_dir():
                roots.append(("user", entry))
    if team_root.exists():
        for entry in team_root.iterdir():
            if entry.is_dir():
                roots.append(("team", entry))
    if global_root.exists():
        roots.append(("global", global_root))
    return roots


def _user_id_from_owner_root(owner_root: Path) -> int:
    slug = str(owner_root.name or "").strip()
    if "-" not in slug:
        return 0
    suffix = slug.rsplit("-", 1)[-1]
    try:
        return int(suffix)
    except Exception:
        return 0


def _existing_resource_uuids(owner_root: Path, owner_scope: str) -> set[str]:
    values: set[str] = set()
    resources_root = owner_root / "resources"
    if not resources_root.exists():
        resources_root_entries = []
    else:
        resources_root_entries = list(resources_root.iterdir())

    for entry in resources_root_entries:
        if not entry.is_dir():
            continue
        resource_uuid = str(entry.name or "").strip()
        if resource_uuid:
            values.add(resource_uuid)

    scope = str(owner_scope or "").strip().lower()
    if scope == "user":
        user_id = _user_id_from_owner_root(owner_root)
        if user_id > 0:
            User = get_user_model()
            user = User.objects.filter(id=user_id, is_active=True).first()
            if user is not None:
                team_ids = list(user.groups.values_list("id", flat=True))
                if team_ids:
                    for resource_uuid in (
                        ResourcePackageOwner.objects.filter(
                            owner_scope=ResourcePackageOwner.OWNER_SCOPE_TEAM,
                            owner_team_id__in=team_ids,
                        )
                        .exclude(resource_uuid__isnull=True)
                        .exclude(resource_uuid="")
                        .values_list("resource_uuid", flat=True)
                    ):
                        value = str(resource_uuid or "").strip()
                        if value:
                            values.add(value)
    return values


def cleanup_stale_knowledge_records() -> dict[str, int]:
    scanned = 0
    removed_knowledge = 0
    removed_snapshots = 0

    for owner_scope, owner_root in _iter_owner_roots():
        scanned += 1
        existing = _existing_resource_uuids(owner_root, owner_scope)

        knowledge_path = owner_root / "knowledge.db"
        scope = str(owner_scope or "").strip().lower()
        if scope == "team":
            # Alpha behavior duplicates team-owned resource knowledge into member KBs.
            # Team knowledge.db is no longer an active source and can be pruned.
            if knowledge_path.exists():
                try:
                    shutil.rmtree(knowledge_path)
                    removed_knowledge += 1
                except Exception:
                    pass
        else:
            collection = None
            if knowledge_path.exists():
                collection = _get_chroma_collection(knowledge_path)
            if collection is not None:
                try:
                    rows = collection.get(include=[])
                    ids = list(rows.get("ids") or [])
                except Exception:
                    ids = []
                stale_ids = [str(item or "").strip() for item in ids if str(item or "").strip() and str(item or "").strip() not in existing]
                if stale_ids:
                    try:
                        collection.delete(ids=stale_ids)
                        removed_knowledge += len(stale_ids)
                    except Exception:
                        pass

        snapshot_db_path = _owner_sqlite_db_path(owner_root, owner_scope)
        if snapshot_db_path.exists():
            conn = _connect_sqlite(snapshot_db_path)
            try:
                _ensure_owner_snapshot_schema(conn)
                rows = conn.execute("SELECT resource_uuid FROM resource_health_snapshots").fetchall()
                stale = []
                for row in rows:
                    resource_uuid = str(row["resource_uuid"] or "").strip()
                    if resource_uuid and resource_uuid not in existing:
                        stale.append(resource_uuid)
                for resource_uuid in stale:
                    conn.execute(
                        "DELETE FROM resource_health_snapshots WHERE resource_uuid = ?",
                        (resource_uuid,),
                    )
                conn.commit()
                removed_snapshots += len(stale)
            finally:
                conn.close()

    return {
        "scanned": int(scanned),
        "removed_knowledge": int(removed_knowledge),
        "removed_snapshots": int(removed_snapshots),
    }
