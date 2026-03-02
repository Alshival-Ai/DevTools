from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
import threading
import time

from django.contrib.auth import get_user_model
from django.db.utils import OperationalError, ProgrammingError

from .github_wiki_sync_service import (
    resource_github_repository_names,
    sync_resource_wiki_with_github,
)
from .internal_cloud_logging import configure_internal_sdk_for_resource
from .models import ResourcePackageOwner, WikiPage
from .resources_store import (
    add_resource,
    get_resource,
    get_resource_by_uuid,
    list_resources,
    update_resource,
)

logger = logging.getLogger(__name__)

DEFAULT_GLOBAL_RESOURCE_NAME = "Alshival"
DEFAULT_GLOBAL_RESOURCE_TYPE = "service"
DEFAULT_GLOBAL_RESOURCE_TARGET = "alshival-platform"
DEFAULT_GLOBAL_RESOURCE_NOTES = "Default global resource for Alshival platform logs."
DEFAULT_GLOBAL_RESOURCE_GITHUB_REPOSITORIES = ["Alshival-Ai/alshival"]

_startup_lock = threading.Lock()
_startup_ran = False
_startup_async_lock = threading.Lock()
_startup_async_started = False
_SKIP_STARTUP_COMMANDS = {
    "check",
    "collectstatic",
    "createsuperuser",
    "dbshell",
    "dumpdata",
    "loaddata",
    "makemigrations",
    "migrate",
    "shell",
    "showmigrations",
    "test",
}
_GITHUB_REPO_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _normalize_github_repository_full_name(raw_value: object) -> str:
    raw = str(raw_value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("https://"):
        raw = raw[8:]
    elif lowered.startswith("http://"):
        raw = raw[7:]
    lowered = raw.lower()
    if lowered.startswith("github.com/"):
        raw = raw[len("github.com/") :]
    raw = raw.strip().strip("/")
    if raw.endswith(".git"):
        raw = raw[:-4]
    parts = [part.strip() for part in raw.split("/") if part.strip()]
    if len(parts) < 2:
        return ""
    owner = parts[0]
    repo = parts[1]
    if not _GITHUB_REPO_PART_RE.fullmatch(owner):
        return ""
    if not _GITHUB_REPO_PART_RE.fullmatch(repo):
        return ""
    return f"{owner}/{repo}"


def _normalize_github_repositories(values: list[object] | tuple[object, ...] | set[object] | object) -> list[str]:
    if isinstance(values, (list, tuple, set)):
        raw_values = list(values)
    else:
        raw_values = [values]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        for piece in re.split(r"[\n,]", str(item or "")):
            full_name = _normalize_github_repository_full_name(piece)
            if not full_name:
                continue
            dedupe_key = full_name.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(full_name)
    return normalized


def _default_global_resource_github_repositories() -> list[str]:
    configured = str(
        os.getenv("ALSHIVAL_DEFAULT_RESOURCE_GITHUB_REPOSITORIES", "")
        or ""
    ).strip()
    if configured:
        normalized = _normalize_github_repositories(configured)
        if normalized:
            return normalized
    return list(DEFAULT_GLOBAL_RESOURCE_GITHUB_REPOSITORIES)


def _merge_github_repositories(existing: list[str], required: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*existing, *required]:
        normalized = _normalize_github_repository_full_name(value)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


def _active_superusers() -> list[object]:
    User = get_user_model()
    return list(
        User.objects.filter(is_active=True, is_superuser=True).order_by("id")
    )


def _find_named_global_resource(*, name: str, owners: list[object]) -> tuple[object | None, str]:
    normalized_name = str(name or "").strip().casefold()
    if not normalized_name:
        return None, ""
    for owner in owners:
        try:
            owner_resources = list_resources(owner)
        except sqlite3.OperationalError:
            continue
        for item in owner_resources:
            resource_name = str(getattr(item, "name", "") or "").strip().casefold()
            if resource_name != normalized_name:
                continue
            resource_uuid = str(getattr(item, "resource_uuid", "") or "").strip()
            if not resource_uuid:
                continue
            owner_row = (
                ResourcePackageOwner.objects.filter(resource_uuid=resource_uuid)
                .only("owner_scope")
                .first()
            )
            if owner_row and owner_row.owner_scope == ResourcePackageOwner.OWNER_SCOPE_GLOBAL:
                return owner, resource_uuid
    return None, ""


def ensure_default_global_resource() -> bool:
    """
    Ensure one default global resource exists for platform-level cloud logs.

    Returns True when the resource is created in this call.
    """
    try:
        superusers = _active_superusers()
    except (OperationalError, ProgrammingError):
        return False
    if not superusers:
        return False
    existing_owner, existing_uuid = _find_named_global_resource(
        name=DEFAULT_GLOBAL_RESOURCE_NAME,
        owners=superusers,
    )
    if existing_owner is not None and existing_uuid:
        return False

    owner = superusers[0]
    required_repositories = _default_global_resource_github_repositories()
    metadata_payload = {"github_repositories": required_repositories} if required_repositories else {}
    try:
        resource_id = add_resource(
            owner,
            name=DEFAULT_GLOBAL_RESOURCE_NAME,
            resource_type=DEFAULT_GLOBAL_RESOURCE_TYPE,
            target=DEFAULT_GLOBAL_RESOURCE_TARGET,
            notes=DEFAULT_GLOBAL_RESOURCE_NOTES,
            resource_metadata=metadata_payload,
            access_scope="global",
            team_names=[],
        )
    except sqlite3.OperationalError:
        return False
    created = get_resource(owner, int(resource_id))
    if created is None or not str(getattr(created, "resource_uuid", "") or "").strip():
        return False
    logger.info(
        "Created default global resource '%s' for owner id=%s.",
        DEFAULT_GLOBAL_RESOURCE_NAME,
        getattr(owner, "id", None),
    )
    return True


def _ensure_default_global_resource_repo_links(owner, resource_uuid: str) -> bool:
    resolved_uuid = str(resource_uuid or "").strip()
    if owner is None or not resolved_uuid:
        return False
    resource = get_resource_by_uuid(owner, resolved_uuid)
    if resource is None:
        return False

    required_repositories = _default_global_resource_github_repositories()
    if not required_repositories:
        return False
    existing_repositories = resource_github_repository_names(resource)
    merged_repositories = _merge_github_repositories(existing_repositories, required_repositories)
    if merged_repositories == existing_repositories:
        return False

    current_metadata = (
        dict(resource.resource_metadata)
        if isinstance(resource.resource_metadata, dict)
        else {}
    )
    current_metadata["github_repositories"] = merged_repositories
    update_resource(
        owner,
        int(resource.id),
        str(resource.name or "").strip(),
        str(resource.resource_type or "").strip(),
        str(resource.target or "").strip(),
        str(resource.notes or "").strip(),
        address=str(resource.address or "").strip(),
        port=str(resource.port or "").strip(),
        db_type=str(resource.db_type or "").strip(),
        healthcheck_url=str(resource.healthcheck_url or "").strip(),
        ssh_key_name=str(resource.ssh_key_name or "").strip(),
        ssh_username=str(resource.ssh_username or "").strip(),
        ssh_key_text=None,
        clear_ssh_key=False,
        ssh_port=str(resource.ssh_port or "").strip(),
        resource_subtype=str(resource.resource_subtype or "").strip(),
        resource_metadata=current_metadata,
        ssh_credential_id=str(resource.ssh_credential_id or "").strip(),
        ssh_credential_scope=str(resource.ssh_credential_scope or "").strip(),
        access_scope=str(resource.access_scope or "").strip() or "account",
        team_names=list(resource.team_names or []),
    )
    logger.info(
        "Updated default global resource '%s' GitHub repositories: %s",
        DEFAULT_GLOBAL_RESOURCE_NAME,
        ", ".join(merged_repositories),
    )
    return True


def _sync_default_global_resource_wiki(owner, resource_uuid: str) -> None:
    if not _env_bool("ALSHIVAL_DEFAULT_RESOURCE_WIKI_SYNC_ON_BOOT", True):
        return
    resolved_uuid = str(resource_uuid or "").strip()
    if owner is None or not resolved_uuid:
        return
    resource = get_resource_by_uuid(owner, resolved_uuid)
    if resource is None:
        return
    repositories = resource_github_repository_names(resource)
    if not repositories:
        return
    result = sync_resource_wiki_with_github(
        actor=owner,
        resource=resource,
        token_users=[owner],
        pull_remote=True,
        push_changes=False,
        reindex_resource_kb=True,
        reindex_check_method="wiki_sync_startup",
    )
    code = str(result.get("code") or "").strip().lower()
    if code in {"ok", "partial_error"}:
        logger.info(
            "Default global resource wiki sync finished code=%s repository=%s kb_reindexed=%s",
            code,
            str(result.get("repository") or "").strip(),
            bool(result.get("kb_reindexed")),
        )
        return
    logger.warning(
        "Default global resource wiki sync unavailable code=%s errors=%s",
        code or "unknown",
        "; ".join(str(item) for item in (result.get("errors") or [])[:3]),
    )


def _default_global_resource_context() -> tuple[object | None, str]:
    try:
        superusers = _active_superusers()
    except (OperationalError, ProgrammingError):
        return None, ""
    if not superusers:
        return None, ""
    owner, resource_uuid = _find_named_global_resource(
        name=DEFAULT_GLOBAL_RESOURCE_NAME,
        owners=superusers,
    )
    return owner, resource_uuid


def _remove_legacy_sdk_workspace_wiki_pages() -> None:
    try:
        legacy_pages = list(
            WikiPage.objects.filter(
                scope=getattr(WikiPage, "SCOPE_WORKSPACE", "workspace"),
                resource_uuid="",
                path__icontains="alshival-sdk",
            ).order_by("id")
        )
    except (OperationalError, ProgrammingError):
        return
    except Exception:
        return
    if not legacy_pages:
        return

    sync_delete = None
    try:
        from .views import _sync_global_workspace_wiki_kb_page as sync_delete
    except Exception:
        sync_delete = None

    legacy_ids: list[int] = []
    for page in legacy_pages:
        page_id = int(getattr(page, "id", 0) or 0)
        if page_id > 0:
            legacy_ids.append(page_id)
        if callable(sync_delete):
            try:
                sync_delete(page=page, force_delete=True)
            except Exception:
                pass
    if not legacy_ids:
        return
    try:
        WikiPage.objects.filter(id__in=legacy_ids).delete()
    except Exception:
        return
    logger.info("Removed legacy SDK workspace wiki pages count=%s.", len(legacy_ids))


def run_startup_initializers_once() -> None:
    global _startup_ran
    with _startup_lock:
        if _startup_ran:
            return
        _startup_ran = True
    try:
        _remove_legacy_sdk_workspace_wiki_pages()
        ensure_default_global_resource()
        owner, resource_uuid = _default_global_resource_context()
        if owner is not None and resource_uuid:
            _ensure_default_global_resource_repo_links(owner, resource_uuid)
            _sync_default_global_resource_wiki(owner, resource_uuid)
            configure_internal_sdk_for_resource(
                owner=owner,
                resource_uuid=resource_uuid,
            )
    except (OperationalError, ProgrammingError):
        # Startup can race migrations during deploys; retry on next process boot.
        return
    except sqlite3.OperationalError:
        # Startup can run in readonly environments (for example some command invocations).
        return
    except Exception:
        logger.exception("Failed running dashboard startup initializers.")


def _should_skip_startup_for_current_command() -> bool:
    if len(sys.argv) < 2:
        return False
    command = str(sys.argv[1] or "").strip().lower()
    return command in _SKIP_STARTUP_COMMANDS


def run_startup_initializers_once_async() -> None:
    global _startup_async_started
    if _should_skip_startup_for_current_command():
        return
    with _startup_async_lock:
        if _startup_async_started:
            return
        _startup_async_started = True

    def _runner() -> None:
        # Let Django finish app initialization before touching the DB.
        time.sleep(0.05)
        run_startup_initializers_once()

    thread = threading.Thread(
        target=_runner,
        name="dashboard-startup-initializers",
        daemon=True,
    )
    thread.start()
