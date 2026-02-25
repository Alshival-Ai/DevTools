from __future__ import annotations

import base64
from contextvars import ContextVar
import hashlib
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from asgiref.sync import sync_to_async
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
import requests
try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    from fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "alshival.settings")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from dashboard.health import check_health  # noqa: E402
from dashboard.models import ResourcePackageOwner, ResourceRouteAlias, ResourceTeamShare  # noqa: E402
from dashboard.request_auth import authenticate_api_key, get_twilio_auth_token, resolve_user_by_phone, user_can_access_resource  # noqa: E402
from dashboard.resources_store import _global_owner_dir, _user_owner_dir, get_resource_by_uuid  # noqa: E402

API_KEY_HEADER = (os.getenv("MCP_API_KEY_HEADER") or "x-api-key").strip() or "x-api-key"
USERNAME_HEADER = (os.getenv("MCP_USERNAME_HEADER") or "x-user-username").strip() or "x-user-username"
EMAIL_HEADER = (os.getenv("MCP_EMAIL_HEADER") or "x-user-email").strip() or "x-user-email"
PHONE_HEADER = (os.getenv("MCP_PHONE_HEADER") or "x-user-phone").strip() or "x-user-phone"
RESOURCE_UUID_HEADER = (os.getenv("MCP_RESOURCE_HEADER") or "x-resource-uuid").strip() or "x-resource-uuid"
GITHUB_MCP_UPSTREAM_URL = (os.getenv("MCP_GITHUB_UPSTREAM_URL") or "").strip()
TWILIO_SIGNATURE_HEADER = (os.getenv("MCP_TWILIO_SIGNATURE_HEADER") or "x-twilio-signature").strip() or "x-twilio-signature"
_REQUEST_AUTH = ContextVar("mcp_request_auth", default=None)


def _ensure_runtime_cache_dirs() -> None:
    candidates = []
    current = str(os.getenv("XDG_CACHE_HOME") or "").strip()
    if current:
        candidates.append(Path(current))
    candidates.append(BASE_DIR / "var" / "cache")
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


def _extract_api_key(request: Request) -> str:
    explicit = (request.headers.get(API_KEY_HEADER) or "").strip()
    if explicit:
        return explicit
    auth_header = (request.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def _set_request_auth_context(
    *,
    auth_scope: str,
    user_id: int = 0,
    username: str = "",
    email: str = "",
    phone: str = "",
) -> None:
    _REQUEST_AUTH.set(
        {
            "auth_scope": str(auth_scope or "").strip(),
            "user_id": int(user_id or 0),
            "username": str(username or "").strip(),
            "email": str(email or "").strip(),
            "phone": str(phone or "").strip(),
        }
    )


def _request_auth_payload() -> dict[str, Any]:
    payload = _REQUEST_AUTH.get()
    if isinstance(payload, dict):
        return payload
    return {}


def _request_actor():
    payload = _request_auth_payload()
    user_id = int(payload.get("user_id", 0) or 0)
    if user_id <= 0:
        return None
    User = get_user_model()
    return User.objects.filter(id=user_id, is_active=True).first()


def _resolve_resource_for_health_check(resource_uuid: str):
    resolved_uuid = str(resource_uuid or "").strip()
    if not resolved_uuid:
        return None, None

    candidate_users: list[object] = []
    seen_user_ids: set[int] = set()

    owner_row = (
        ResourcePackageOwner.objects.select_related("owner_user")
        .filter(resource_uuid=resolved_uuid)
        .first()
    )
    if owner_row and owner_row.owner_user_id and owner_row.owner_user and bool(owner_row.owner_user.is_active):
        candidate_users.append(owner_row.owner_user)
        seen_user_ids.add(int(owner_row.owner_user_id))

    for row in (
        ResourceRouteAlias.objects.select_related("owner_user")
        .filter(resource_uuid=resolved_uuid, owner_user_id__isnull=False)
        .order_by("-is_current", "-updated_at")
    ):
        owner_user = row.owner_user
        if owner_user is None or not bool(owner_user.is_active):
            continue
        owner_user_id = int(owner_user.id)
        if owner_user_id in seen_user_ids:
            continue
        candidate_users.append(owner_user)
        seen_user_ids.add(owner_user_id)

    actor = _request_actor()
    if actor is not None:
        actor_id = int(getattr(actor, "id", 0) or 0)
        if actor_id > 0 and actor_id not in seen_user_ids:
            candidate_users.append(actor)
            seen_user_ids.add(actor_id)

    User = get_user_model()
    for user in User.objects.filter(is_active=True).order_by("id"):
        user_id = int(user.id)
        if user_id in seen_user_ids:
            continue
        candidate_users.append(user)
        seen_user_ids.add(user_id)

    for owner_user in candidate_users:
        try:
            resource = get_resource_by_uuid(owner_user, resolved_uuid)
        except Exception:
            continue
        if resource is not None:
            return owner_user, resource
    return None, None


def _actor_can_check_resource(*, actor, owner_user, resource_uuid: str) -> bool:
    if actor is None:
        return False
    if bool(getattr(actor, "is_superuser", False)):
        return True

    resolved_uuid = str(resource_uuid or "").strip()
    if not resolved_uuid:
        return False

    owner_row = (
        ResourcePackageOwner.objects.select_related("owner_team")
        .filter(resource_uuid=resolved_uuid)
        .first()
    )
    if owner_row and str(getattr(owner_row, "owner_scope", "")).strip().lower() == ResourcePackageOwner.OWNER_SCOPE_GLOBAL:
        return True

    if owner_user is not None and int(getattr(actor, "id", 0) or 0) == int(getattr(owner_user, "id", 0) or 0):
        return True

    actor_team_ids = list(actor.groups.values_list("id", flat=True))
    if not actor_team_ids:
        return False
    if owner_user is None:
        return False
    return ResourceTeamShare.objects.filter(
        owner=owner_user,
        resource_uuid=resolved_uuid,
        team_id__in=actor_team_ids,
    ).exists()


def _twilio_signature(url: str, params: list[tuple[str, str]], auth_token: str) -> str:
    payload = str(url or "")
    for key, value in sorted(params, key=lambda item: (item[0], item[1])):
        payload += f"{key}{value}"
    digest = hmac.new(
        str(auth_token or "").encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _request_url_candidates(request: Request) -> list[str]:
    raw = str(request.url)
    parsed = urlsplit(raw)
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").strip()
    candidates = [raw]
    if forwarded_proto or forwarded_host:
        candidates.append(
            urlunsplit(
                (
                    forwarded_proto or parsed.scheme,
                    forwarded_host or parsed.netloc,
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
        )
    # Twilio signature behavior can vary across proxies; also try versions without query string.
    candidates.extend(
        [
            urlunsplit((urlsplit(item).scheme, urlsplit(item).netloc, urlsplit(item).path, "", ""))
            for item in list(candidates)
        ]
    )
    seen: set[str] = set()
    deduped: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


async def _twilio_form_params(request: Request) -> list[tuple[str, str]]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" not in content_type:
        return []
    body = await request.body()
    if not body:
        return []
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return []
    return [(str(key or ""), str(value or "")) for key, value in parse_qsl(decoded, keep_blank_values=True)]


async def _authenticate_twilio_phone_request(request: Request) -> tuple[bool, str]:
    twilio_sig = (request.headers.get(TWILIO_SIGNATURE_HEADER) or "").strip()
    if not twilio_sig:
        return False, "missing_twilio_signature"

    auth_token = await sync_to_async(get_twilio_auth_token, thread_sensitive=True)()
    if not auth_token:
        return False, "twilio_not_configured"

    params = await _twilio_form_params(request)
    expected_matches = [
        hmac.compare_digest(_twilio_signature(url, params, auth_token), twilio_sig)
        for url in _request_url_candidates(request)
    ]
    if not any(expected_matches):
        return False, "invalid_twilio_signature"

    param_lookup = {str(key).lower(): str(value or "") for key, value in params}
    phone = (
        (request.headers.get(PHONE_HEADER) or "").strip()
        or str(param_lookup.get("from") or "").strip()
    )
    if not phone:
        return False, "missing_phone_identity"

    user = await sync_to_async(resolve_user_by_phone, thread_sensitive=True)(phone)
    if user is None:
        return False, "unknown_phone_identity"

    resource_uuid = (request.headers.get(RESOURCE_UUID_HEADER) or "").strip()
    if resource_uuid:
        allowed = await sync_to_async(user_can_access_resource, thread_sensitive=True)(
            user=user,
            resource_uuid=resource_uuid,
        )
        if not allowed:
            return False, "resource_access_denied"

    request.state.auth_user_id = int(getattr(user, "id", 0) or 0)
    request.state.auth_username = str(getattr(user, "username", "") or "")
    request.state.auth_email = str(getattr(user, "email", "") or "")
    request.state.auth_phone = phone
    request.state.auth_scope = "twilio_phone"
    return True, ""


mcp = FastMCP("alshival-mcp", stateless_http=True)


@mcp.tool()
def ping() -> dict[str, str]:
    """Dummy MCP tool used to validate MCP auth wiring."""
    return {
        "ok": "true",
        "message": "pong",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def _query_chroma_resources(
    *,
    knowledge_path: Path,
    query: str,
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    _ensure_runtime_cache_dirs()
    try:
        import chromadb
    except Exception:
        return [], "chromadb package is not installed"

    resolved_path = Path(knowledge_path)
    if not resolved_path.exists():
        return [], ""

    client = chromadb.PersistentClient(path=str(resolved_path))
    try:
        collection = client.get_collection(name="resources")
    except Exception:
        return [], ""

    n_results = max(1, min(int(limit or 5), 50))
    where_filter: dict[str, Any] | None = None

    resolved_query = str(query or "").strip()
    rows: list[dict[str, Any]] = []
    if resolved_query:
        try:
            payload = collection.query(
                query_texts=[resolved_query],
                n_results=n_results,
                where=where_filter,
            )
        except Exception as exc:
            return [], f"chroma query failed: {exc}"
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
    else:
        try:
            payload = collection.get(where=where_filter, limit=n_results)
        except Exception as exc:
            return [], f"chroma get failed: {exc}"
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


@mcp.tool()
def search_kb(
    query: str = "",
) -> dict[str, Any]:
    """
    Search both personal and global knowledge bases for the authenticated user.
    Returns up to 4 personal matches and 3 global matches.
    """
    actor = _request_actor()
    if actor is None:
        return {"ok": False, "error": "authenticated user identity is required", "results": []}

    personal_path = _user_owner_dir(actor) / "knowledge.db"
    global_path = _global_owner_dir() / "knowledge.db"

    personal_results, personal_error = _query_chroma_resources(
        knowledge_path=personal_path,
        query=query,
        limit=4,
    )
    if personal_error:
        return {"ok": False, "error": personal_error, "results": []}

    global_results, global_error = _query_chroma_resources(
        knowledge_path=global_path,
        query=query,
        limit=3,
    )
    if global_error:
        return {"ok": False, "error": global_error, "results": []}

    merged = list(personal_results) + list(global_results)
    return {
        "ok": True,
        "collection": "resources",
        "knowledge_paths": {
            "user": str(personal_path),
            "global": str(global_path),
        },
        "query": str(query or ""),
        "user_limit": 4,
        "global_limit": 3,
        "user_result_count": len(personal_results),
        "global_result_count": len(global_results),
        "result_count": len(merged),
        "user_results": personal_results,
        "global_results": global_results,
        "results": merged,
    }


@mcp.tool()
def resource_health_check(resource_uuid: str) -> dict[str, Any]:
    """
    Run a health check for a resource and return the latest status details.

    Access policy:
    - superuser: allowed
    - global resources: allowed for any authenticated user
    - user resources: owner only
    - team-shared resources: members of shared teams
    """
    resolved_uuid = str(resource_uuid or "").strip()
    if not resolved_uuid:
        return {"ok": False, "error": "resource_uuid is required"}

    actor = _request_actor()
    if actor is None:
        return {"ok": False, "error": "authenticated user identity is required"}

    owner_user, resource = _resolve_resource_for_health_check(resolved_uuid)
    if owner_user is None or resource is None:
        return {"ok": False, "error": f"resource not found: {resolved_uuid}"}

    if not _actor_can_check_resource(actor=actor, owner_user=owner_user, resource_uuid=resolved_uuid):
        return {"ok": False, "error": f"access denied for resource: {resolved_uuid}"}

    try:
        result = check_health(int(resource.id), user=owner_user, emit_transition_log=True)
    except Exception as exc:
        return {"ok": False, "error": f"health check failed: {exc}"}

    return {
        "ok": True,
        "resource_uuid": resolved_uuid,
        "resource_name": str(getattr(resource, "name", "") or ""),
        "owner_username": str(getattr(owner_user, "username", "") or ""),
        "status": str(result.status or ""),
        "checked_at": str(result.checked_at or ""),
        "target": str(result.target or ""),
        "error": str(result.error or ""),
        "check_method": str(result.check_method or ""),
        "latency_ms": result.latency_ms,
        "packet_loss_pct": result.packet_loss_pct,
    }


mcp.settings.streamable_http_path = "/"
mcp_app = mcp.streamable_http_app()
app = FastAPI(lifespan=lambda app: mcp.session_manager.run())
app.mount("/mcp/", mcp_app)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _proxy_headers(request: Request) -> dict[str, str]:
    forwarded: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in {"host", "content-length"}:
            continue
        forwarded[key] = value
    return forwarded


def _proxy_mcp_request(*, request: Request, upstream_url: str, body: bytes, suffix: str = ""):
    resolved_base = str(upstream_url or "").strip()
    if not resolved_base:
        return JSONResponse({"detail": "MCP upstream is not configured"}, status_code=503)
    target_url = urljoin(resolved_base.rstrip("/") + "/", suffix.lstrip("/"))
    try:
        response = requests.request(
            method=request.method,
            url=target_url,
            params=request.query_params,
            headers=_proxy_headers(request),
            data=body,
            timeout=60,
        )
    except requests.RequestException as exc:
        return JSONResponse({"detail": f"MCP upstream request failed: {exc}"}, status_code=502)

    content_type = response.headers.get("content-type") or ""
    if "application/json" in content_type.lower():
        try:
            payload = response.json()
        except Exception:
            payload = {"detail": response.text}
        return JSONResponse(payload, status_code=response.status_code)

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=content_type or None,
    )


@app.api_route("/github/", methods=["GET", "POST"])
@app.api_route("/github/{path:path}", methods=["GET", "POST"])
async def github_proxy(request: Request, path: str = ""):
    body = await request.body() if request.method in {"POST", "PUT", "PATCH"} else b""
    return _proxy_mcp_request(
        request=request,
        upstream_url=GITHUB_MCP_UPSTREAM_URL,
        body=body,
        suffix=path,
    )


@app.middleware("http")
async def require_global_api_key(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path == "/health":
        return await call_next(request)
    token = _REQUEST_AUTH.set(None)

    api_key = _extract_api_key(request)
    if not api_key:
        # Only attempt Twilio auth when a Twilio signature header is present.
        # This keeps regular requests independent from Twilio config/state.
        twilio_signature = (request.headers.get(TWILIO_SIGNATURE_HEADER) or "").strip()
        if twilio_signature:
            twilio_ok, twilio_error = await _authenticate_twilio_phone_request(request)
            if not twilio_ok:
                try:
                    return JSONResponse(
                        {
                            "detail": "Invalid Twilio authentication",
                            "twilio_error": twilio_error,
                        },
                        status_code=401,
                    )
                finally:
                    _REQUEST_AUTH.reset(token)
            _set_request_auth_context(
                auth_scope=str(getattr(request.state, "auth_scope", "twilio_phone")),
                user_id=int(getattr(request.state, "auth_user_id", 0) or 0),
                username=str(getattr(request.state, "auth_username", "") or ""),
                email=str(getattr(request.state, "auth_email", "") or ""),
                phone=str(getattr(request.state, "auth_phone", "") or ""),
            )
            try:
                return await call_next(request)
            finally:
                _REQUEST_AUTH.reset(token)
        try:
            return JSONResponse(
                {
                    "detail": f"Missing API key (expected {API_KEY_HEADER})",
                },
                status_code=401,
            )
        finally:
            _REQUEST_AUTH.reset(token)

    username = (request.headers.get(USERNAME_HEADER) or "").strip()
    email = (request.headers.get(EMAIL_HEADER) or "").strip()
    phone = (request.headers.get(PHONE_HEADER) or "").strip()
    resource_uuid = (request.headers.get(RESOURCE_UUID_HEADER) or "").strip()
    auth = await sync_to_async(authenticate_api_key, thread_sensitive=True)(
        api_key=api_key,
        username=username,
        email=email,
        phone=phone,
        resource_uuid=resource_uuid,
        require_resource_access=bool(resource_uuid),
    )
    if not auth.ok:
        try:
            return JSONResponse({"detail": "Invalid API key"}, status_code=401)
        finally:
            _REQUEST_AUTH.reset(token)

    request.state.auth_scope = auth.key_scope
    if auth.user is not None:
        request.state.auth_user_id = int(getattr(auth.user, "id", 0) or 0)
        request.state.auth_username = str(getattr(auth.user, "username", "") or "")
        request.state.auth_email = str(getattr(auth.user, "email", "") or "")

    _set_request_auth_context(
        auth_scope=auth.key_scope,
        user_id=int(getattr(request.state, "auth_user_id", 0) or 0),
        username=str(getattr(request.state, "auth_username", "") or ""),
        email=str(getattr(request.state, "auth_email", "") or ""),
        phone=str(getattr(request.state, "auth_phone", "") or ""),
    )
    try:
        return await call_next(request)
    finally:
        _REQUEST_AUTH.reset(token)
