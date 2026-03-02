from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from django.conf import settings

from .global_api_key_store import create_global_team_api_key

logger = logging.getLogger(__name__)

_SDK_RUNTIME_MODULE = "alshival_sdk_runtime"
_SDK_MODULE_CACHE: Any | None = None
_SDK_MODULE_LOCK = threading.Lock()
_KEY_ROTATION_THREAD_LOCK = threading.Lock()
_KEY_ROTATION_THREAD_STARTED = False

_VALID_CLOUD_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "ALERT", "NONE"}


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _normalize_cloud_level(value: str) -> str:
    candidate = str(value or "").strip().upper()
    if candidate in _VALID_CLOUD_LEVELS:
        return candidate
    return "ERROR"


def _owner_username(owner) -> str:
    direct = str(getattr(owner, "username", "") or "").strip()
    if direct:
        return direct
    getter = getattr(owner, "get_username", None)
    if callable(getter):
        return str(getter() or "").strip()
    return ""


def _local_sdk_base_url() -> str:
    explicit = (
        str(os.getenv("ALSHIVAL_SDK_LOCAL_BASE_URL", "") or "").strip()
        or str(os.getenv("ALSHIVAL_INTERNAL_BASE_URL", "") or "").strip()
    )
    if explicit:
        parsed = urlsplit(explicit)
        if parsed.scheme and parsed.netloc:
            return explicit.rstrip("/")

    host = str(os.getenv("ALSHIVAL_SDK_LOCAL_HOST", "") or "").strip() or "127.0.0.1"
    port = str(os.getenv("ALSHIVAL_SDK_LOCAL_PORT", "") or "").strip() or str(os.getenv("PORT", "") or "").strip() or "8000"
    scheme = str(os.getenv("ALSHIVAL_SDK_LOCAL_SCHEME", "") or "").strip().lower() or "http"
    if scheme not in {"http", "https"}:
        scheme = "http"
    return f"{scheme}://{host}:{port}"


def _build_local_resource_url(owner, resource_uuid: str) -> str:
    username = _owner_username(owner)
    resolved_uuid = str(resource_uuid or "").strip()
    if not username or not resolved_uuid:
        return ""
    base_url = _local_sdk_base_url()
    if not base_url:
        return ""
    encoded_username = quote(username, safe="")
    encoded_uuid = quote(resolved_uuid, safe="")
    return f"{base_url}/u/{encoded_username}/resources/{encoded_uuid}/"


def _candidate_sdk_init_paths() -> list[Path]:
    candidates: list[Path] = []
    try:
        dist = importlib.metadata.distribution("alshival")
        dist_path = Path(dist.locate_file("alshival"))
        candidates.append(dist_path / "__init__.py")
    except Exception:
        pass

    local_repo_init = Path(settings.BASE_DIR) / "alshival-sdk" / "src" / "alshival" / "__init__.py"
    candidates.append(local_repo_init)
    return candidates


def _load_sdk_module() -> Any | None:
    global _SDK_MODULE_CACHE
    with _SDK_MODULE_LOCK:
        if _SDK_MODULE_CACHE is not None:
            return _SDK_MODULE_CACHE
        if _SDK_RUNTIME_MODULE in sys.modules:
            _SDK_MODULE_CACHE = sys.modules[_SDK_RUNTIME_MODULE]
            return _SDK_MODULE_CACHE

        for init_path in _candidate_sdk_init_paths():
            try:
                resolved_init = Path(init_path).resolve()
            except Exception:
                continue
            if not resolved_init.is_file():
                continue
            package_dir = resolved_init.parent
            spec = importlib.util.spec_from_file_location(
                _SDK_RUNTIME_MODULE,
                str(resolved_init),
                submodule_search_locations=[str(package_dir)],
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[_SDK_RUNTIME_MODULE] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(_SDK_RUNTIME_MODULE, None)
                continue
            _SDK_MODULE_CACHE = module
            return module
    return None


def _sdk_logger_names() -> list[str]:
    raw = str(os.getenv("ALSHIVAL_SDK_LOGGER_NAMES", "") or "").strip()
    if not raw:
        return ["dashboard", "alshival"]
    names: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        candidate = str(part or "").strip()
        if not candidate or candidate in seen:
            continue
        names.append(candidate)
        seen.add(candidate)
    return names or ["dashboard", "alshival"]


def _attach_sdk_handlers(sdk_module, *, cloud_level: str) -> None:
    for logger_name in _sdk_logger_names():
        target = logging.getLogger(logger_name)
        if target.level == logging.NOTSET or target.getEffectiveLevel() > logging.INFO:
            target.setLevel(logging.INFO)
        sdk_module.attach(target)
    if _env_bool("ALSHIVAL_SDK_ATTACH_ROOT", False):
        sdk_module.attach(logging.getLogger())
    logger.info(
        "Attached Alshival SDK cloud handlers to loggers=%s cloud_level=%s.",
        ",".join(_sdk_logger_names()),
        cloud_level,
    )


def _rotate_runtime_global_key(owner) -> str:
    key_name = (
        str(os.getenv("ALSHIVAL_SDK_GLOBAL_KEY_NAME", "") or "").strip()
        or "Internal SDK Global API Key"
    )
    _key_id, raw_key = create_global_team_api_key(
        user=owner,
        name=key_name,
        team_name="",
    )
    return str(raw_key or "").strip()


def _refresh_sdk_api_key_loop(*, owner, username: str, resource_url: str, cloud_level: str) -> None:
    sdk_module = _load_sdk_module()
    if sdk_module is None:
        return

    try:
        interval_seconds = int(str(os.getenv("ALSHIVAL_SDK_KEY_ROTATE_INTERVAL_SECONDS", "") or "").strip() or "2700")
    except Exception:
        interval_seconds = 2700
    interval_seconds = max(300, interval_seconds)

    while True:
        time.sleep(interval_seconds)
        try:
            if str(os.getenv("ALSHIVAL_SDK_MANAGED_API_KEY", "") or "").strip() != "1":
                # Respect explicit static key if it appears later.
                continue
            rotated_key = _rotate_runtime_global_key(owner)
            if not rotated_key:
                continue
            os.environ["ALSHIVAL_API_KEY"] = rotated_key
            sdk_module.configure(
                username=username,
                resource=resource_url,
                api_key=rotated_key,
                cloud_level=cloud_level,
            )
            logger.info("Rotated internal Alshival SDK global API key.")
        except Exception:
            logger.exception("Failed rotating internal Alshival SDK global API key.")


def _start_key_rotation_thread(*, owner, username: str, resource_url: str, cloud_level: str) -> None:
    global _KEY_ROTATION_THREAD_STARTED
    if not _env_bool("ALSHIVAL_SDK_AUTO_ROTATE_KEY", True):
        return
    with _KEY_ROTATION_THREAD_LOCK:
        if _KEY_ROTATION_THREAD_STARTED:
            return
        _KEY_ROTATION_THREAD_STARTED = True
    thread = threading.Thread(
        target=_refresh_sdk_api_key_loop,
        kwargs={
            "owner": owner,
            "username": username,
            "resource_url": resource_url,
            "cloud_level": cloud_level,
        },
        name="alshival-sdk-key-rotation",
        daemon=True,
    )
    thread.start()


def configure_internal_sdk_for_resource(*, owner, resource_uuid: str) -> bool:
    if owner is None:
        return False
    if not _env_bool("ALSHIVAL_SDK_AUTO_CONFIG", True):
        return False

    resolved_uuid = str(resource_uuid or "").strip()
    username = str(os.getenv("ALSHIVAL_USERNAME", "") or "").strip() or _owner_username(owner)
    if not resolved_uuid or not username:
        return False

    resource_url = str(os.getenv("ALSHIVAL_RESOURCE", "") or "").strip() or _build_local_resource_url(owner, resolved_uuid)
    if not resource_url:
        return False

    cloud_level = _normalize_cloud_level(
        str(os.getenv("ALSHIVAL_CLOUD_LEVEL", "") or "").strip() or "ERROR"
    )
    api_key = str(os.getenv("ALSHIVAL_API_KEY", "") or "").strip()
    generated_key = False
    if not api_key:
        try:
            api_key = _rotate_runtime_global_key(owner)
            generated_key = bool(api_key)
        except Exception:
            logger.exception("Unable to generate runtime global API key for SDK cloud logs.")
            return False
    if not api_key:
        return False

    os.environ.setdefault("ALSHIVAL_USERNAME", username)
    os.environ.setdefault("ALSHIVAL_RESOURCE", resource_url)
    os.environ.setdefault("ALSHIVAL_CLOUD_LEVEL", cloud_level)
    os.environ["ALSHIVAL_API_KEY"] = api_key
    os.environ["ALSHIVAL_SDK_MANAGED_API_KEY"] = "1" if generated_key else "0"

    sdk_module = _load_sdk_module()
    if sdk_module is None:
        logger.warning("Alshival SDK package is not available; internal cloud logs disabled.")
        return False

    try:
        sdk_module.configure(
            username=username,
            resource=resource_url,
            api_key=api_key,
            cloud_level=cloud_level,
        )
        _attach_sdk_handlers(sdk_module, cloud_level=cloud_level)
        logger.info(
            "Configured internal Alshival SDK cloud logging resource=%s username=%s cloud_level=%s.",
            resource_url,
            username,
            cloud_level,
        )
    except Exception:
        logger.exception("Failed configuring internal Alshival SDK cloud logging.")
        return False

    if generated_key:
        _start_key_rotation_thread(
            owner=owner,
            username=username,
            resource_url=resource_url,
            cloud_level=cloud_level,
        )
    return True


def get_internal_sdk_module():
    return _load_sdk_module()
