from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import requests
import socket
import subprocess
from shutil import which
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model

from dashboard.knowledge_store import upsert_resource_health_knowledge
from dashboard.models import ResourcePackageOwner, ResourceTeamShare, UserNotificationSettings
from dashboard.request_context import get_current_user
from dashboard.resources_store import (
    add_ask_chat_context_event,
    add_user_notification,
    get_user_alert_filter_prompt,
    get_resource_alert_settings,
    get_resource,
    log_resource_check,
    store_resource_logs,
    update_resource_health,
)
from dashboard.support_inbox import send_support_inbox_email
from dashboard.setup_state import (
    get_setup_state,
    is_global_monitoring_enabled,
    is_support_inbox_email_alerts_enabled,
    is_twilio_configured,
)


STATUS_HEALTHY = "healthy"
STATUS_UNHEALTHY = "unhealthy"
STATUS_UNKNOWN = "unknown"


@dataclass
class HealthResult:
    resource_id: int
    status: str
    checked_at: str
    target: str
    error: str
    check_method: str
    latency_ms: float | None
    packet_loss_pct: float | None


def _normalize_phone(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    keep_plus = raw.startswith("+")
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""
    return f"+{digits}" if keep_plus else digits


def _resource_alert_recipients(owner_user, resource_uuid: str) -> list[object]:
    if owner_user is None:
        return []

    User = get_user_model()
    resolved_uuid = str(resource_uuid or "").strip()
    recipients: dict[int, object] = {}

    def _add_user(user_obj) -> None:
        if user_obj is None:
            return
        user_id = int(getattr(user_obj, "id", 0) or 0)
        if user_id <= 0:
            return
        if not bool(getattr(user_obj, "is_active", False)):
            return
        recipients[user_id] = user_obj

    _add_user(owner_user)
    owner_row = (
        ResourcePackageOwner.objects.select_related("owner_team")
        .filter(resource_uuid=resolved_uuid)
        .first()
    )
    if owner_row is not None:
        owner_scope = str(getattr(owner_row, "owner_scope", "") or "").strip().lower()
        if owner_scope == ResourcePackageOwner.OWNER_SCOPE_GLOBAL:
            for user_obj in User.objects.filter(is_active=True).order_by("id"):
                _add_user(user_obj)
        elif owner_scope == ResourcePackageOwner.OWNER_SCOPE_TEAM and getattr(owner_row, "owner_team_id", None):
            owner_team = getattr(owner_row, "owner_team", None)
            if owner_team is not None:
                for user_obj in owner_team.user_set.filter(is_active=True).order_by("id"):
                    _add_user(user_obj)

    team_ids = list(
        ResourceTeamShare.objects.filter(owner=owner_user, resource_uuid=resolved_uuid)
        .values_list("team_id", flat=True)
        .distinct()
    )
    if team_ids:
        for user_obj in User.objects.filter(is_active=True, groups__id__in=team_ids).distinct().order_by("id"):
            _add_user(user_obj)

    return [recipients[key] for key in sorted(recipients.keys())]


def _twilio_sms_credentials() -> tuple[str, str, str]:
    setup = get_setup_state()
    account_sid = str(getattr(setup, "twilio_account_sid", "") or "").strip() if setup else ""
    auth_token = str(getattr(setup, "twilio_auth_token", "") or "").strip() if setup else ""
    from_number = str(getattr(setup, "twilio_from_number", "") or "").strip() if setup else ""
    if not account_sid:
        account_sid = str(os.getenv("TWILIO_ACCOUNT_SID", "") or "").strip()
    if not auth_token:
        auth_token = str(os.getenv("TWILIO_AUTH_TOKEN", "") or "").strip()
    if not from_number:
        from_number = str(os.getenv("TWILIO_FROM_NUMBER", "") or "").strip()
    return account_sid, auth_token, from_number


def _send_transition_sms(*, recipient, message: str) -> tuple[bool, str]:
    if not is_twilio_configured():
        return False, "twilio_not_configured"
    account_sid, auth_token, from_number = _twilio_sms_credentials()
    if not (account_sid and auth_token and from_number):
        return False, "twilio_not_configured"

    phone_raw = (
        UserNotificationSettings.objects.filter(user=recipient)
        .values_list("phone_number", flat=True)
        .first()
        or ""
    )
    to_number = _normalize_phone(phone_raw)
    if not to_number:
        return False, "missing_phone_number"

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            data={
                "To": to_number,
                "From": from_number,
                "Body": str(message or "").strip()[:1200],
            },
            auth=(account_sid, auth_token),
            timeout=10,
        )
    except requests.RequestException as exc:
        return False, f"twilio_request_failed:{exc}"
    if 200 <= int(response.status_code) < 300:
        return True, ""
    return False, f"twilio_status_{int(response.status_code)}"


def _send_transition_email(*, recipient, subject: str, message: str) -> tuple[bool, str]:
    if not is_support_inbox_email_alerts_enabled():
        return False, "support_inbox_email_disabled"
    recipient_email = str(getattr(recipient, "email", "") or "").strip()
    if not recipient_email:
        return False, "missing_email_address"
    return send_support_inbox_email(
        recipient_email=recipient_email,
        subject=str(subject or "").strip(),
        body_text=str(message or "").strip(),
    )


def _normalize_alert_channels(channels: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for channel in channels:
        candidate = str(channel or "").strip().lower()
        if candidate not in {"app", "sms", "email"}:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _log_alert_context_event_safe(*, recipient, event_type: str, summary: str, payload: dict[str, object]) -> None:
    try:
        add_ask_chat_context_event(
            recipient,
            event_type=event_type,
            summary=summary,
            payload=payload,
            conversation_id="default",
        )
    except Exception:
        return


def _extract_chat_completion_text(payload: dict[str, object] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "").strip()


def _parse_json_object(raw_text: str) -> dict[str, object]:
    text = str(raw_text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _alert_filter_openai_config() -> tuple[str, str]:
    setup = get_setup_state()
    api_key = str(getattr(setup, "openai_api_key", "") or "").strip()
    if not api_key:
        api_key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()
    model = (
        str(getattr(settings, "ALSHIVAL_OPENAI_CHAT_MODEL", "") or "").strip()
        or str(getattr(setup, "default_model", "") or "").strip()
        or "gpt-4o-mini"
    )
    return api_key, model


def _alert_filter_allowed_channels(
    *,
    recipient,
    alert_kind: str,
    candidate_channels: list[str],
    subject: str,
    body: str,
    context: dict[str, object] | None = None,
) -> set[str]:
    candidates = _normalize_alert_channels(candidate_channels)
    if not candidates:
        return set()

    preferences = get_user_alert_filter_prompt(recipient)
    custom_prompt = str(preferences.get("prompt") or "").strip()
    if not custom_prompt:
        return set(candidates)

    api_key, model = _alert_filter_openai_config()
    if not api_key:
        return set(candidates)

    context_payload = context if isinstance(context, dict) else {}
    request_payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "\n".join(
                    [
                        "You are Alshival's alert_filter agent.",
                        "Decide which channels should receive this alert for this user.",
                        "Return strict JSON only.",
                        '{"allowed_channels":["app","sms","email"],"reason":"short reason"}',
                        "Rules:",
                        "- allowed_channels must be a subset of candidate_channels.",
                        "- Use the user preferences as highest priority.",
                        "- If the preferences do not clearly block this alert, allow it.",
                        "- Do not add channels that are not listed in candidate_channels.",
                        "",
                        "User alert preferences:",
                        custom_prompt,
                    ]
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "alert_kind": str(alert_kind or "").strip(),
                        "candidate_channels": candidates,
                        "subject": str(subject or "").strip()[:255],
                        "body": str(body or "").strip()[:3000],
                        "context": context_payload,
                        "recipient": {
                            "id": int(getattr(recipient, "id", 0) or 0),
                            "username": str(getattr(recipient, "username", "") or "").strip(),
                            "email": str(getattr(recipient, "email", "") or "").strip(),
                        },
                    },
                    separators=(",", ":"),
                ),
            },
        ],
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=20,
        )
    except requests.RequestException:
        return set(candidates)
    if response.status_code >= 400:
        return set(candidates)

    data = response.json() if response.content else {}
    content = _extract_chat_completion_text(data)
    parsed = _parse_json_object(content)
    if not parsed:
        return set(candidates)

    allowed_raw = parsed.get("allowed_channels")
    if isinstance(allowed_raw, list):
        allowed = {
            channel
            for channel in _normalize_alert_channels([str(item or "") for item in allowed_raw])
            if channel in candidates
        }
        return allowed

    blocked_raw = parsed.get("blocked_channels")
    if isinstance(blocked_raw, list):
        blocked = {
            channel
            for channel in _normalize_alert_channels([str(item or "") for item in blocked_raw])
            if channel in candidates
        }
        return set(candidates) - blocked

    send_value = parsed.get("send")
    if isinstance(send_value, bool):
        return set(candidates) if send_value else set()
    if isinstance(send_value, str):
        lowered = send_value.strip().lower()
        if lowered in {"false", "0", "no"}:
            return set()
        if lowered in {"true", "1", "yes"}:
            return set(candidates)
    return set(candidates)


def _extract_cloud_log_alert_entries(payload: dict[str, object] | None) -> list[dict[str, str]]:
    source = payload if isinstance(payload, dict) else {}
    logs = source.get("logs")
    candidates: list[object]
    if isinstance(logs, list):
        candidates = logs
    else:
        candidates = [source]

    matched: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or "").strip().lower()
        if level not in {"error", "alert"}:
            continue
        matched.append(
            {
                "level": level,
                "message": str(item.get("message") or "").strip(),
                "logger": str(item.get("logger") or "alshival").strip() or "alshival",
                "ts": str(item.get("ts") or "").strip(),
            }
        )
    return matched


def dispatch_cloud_log_error_alerts(*, user, resource, payload: dict[str, object] | None) -> int:
    if user is None or resource is None:
        return 0
    entries = _extract_cloud_log_alert_entries(payload)
    if not entries:
        return 0

    levels = [str(item.get("level") or "error").lower() for item in entries]
    highest_level = "alert" if "alert" in levels else "error"
    now_iso = datetime.now(timezone.utc).isoformat()
    envelope_received_at = str((payload or {}).get("received_at") or "").strip() if isinstance(payload, dict) else ""
    received_at = envelope_received_at or now_iso
    subject = f"[Alshival] Cloud log {highest_level.upper()}: {resource.name}"

    body_lines = [
        f"Resource: {resource.name}",
        f"UUID: {resource.resource_uuid}",
        f"Received at: {received_at}",
        f"Matched log entries: {len(entries)}",
    ]
    for item in entries[:5]:
        level = str(item.get("level") or "error").upper()
        logger = str(item.get("logger") or "alshival")
        message = str(item.get("message") or "").strip()
        if len(message) > 240:
            message = f"{message[:237]}..."
        body_lines.append(f"- [{level}] {logger}: {message}")
    body = "\n".join(body_lines)
    sms_body = (
        f"{resource.name}: {len(entries)} cloud log {highest_level}"
        + (f" ({str(entries[0].get('message') or '')[:80]})" if entries else "")
    )

    dispatched = 0
    recipients = _resource_alert_recipients(user, resource.resource_uuid)
    for recipient in recipients:
        recipient_id = int(getattr(recipient, "id", 0) or 0)
        if recipient_id <= 0:
            continue
        settings_payload = get_resource_alert_settings(user, resource.resource_uuid, recipient_id)
        candidate_channels: list[str] = []
        if bool(settings_payload.get("cloud_log_errors_app_enabled", True)):
            candidate_channels.append("app")
        if bool(settings_payload.get("cloud_log_errors_sms_enabled", False)):
            candidate_channels.append("sms")
        if bool(settings_payload.get("cloud_log_errors_email_enabled", False)):
            candidate_channels.append("email")
        allowed_channels = _alert_filter_allowed_channels(
            recipient=recipient,
            alert_kind="cloud_log_error",
            candidate_channels=candidate_channels,
            subject=subject,
            body=body,
            context={
                "resource_uuid": str(resource.resource_uuid or "").strip(),
                "resource_name": str(resource.name or "").strip(),
                "received_at": received_at,
                "entry_count": len(entries),
                "highest_level": highest_level,
                "sample_entries": entries[:3],
            },
        )
        if not allowed_channels:
            continue

        if "app" in allowed_channels:
            notification_id = add_user_notification(
                recipient,
                kind="cloud_log_alert",
                title=subject,
                body=body,
                resource_uuid=str(resource.resource_uuid or "").strip(),
                level="error" if highest_level == "alert" else "warning",
                channel="app",
                metadata={
                    "source": "resource_logs_ingest",
                    "recipient_user_id": recipient_id,
                    "recipient_username": str(getattr(recipient, "username", "") or ""),
                    "highest_level": highest_level,
                    "entry_count": len(entries),
                    "received_at": received_at,
                },
            )
            if notification_id:
                dispatched += 1
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_cloud_log_app",
                    summary=f"Cloud-log alert for {resource.name} delivered in app.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "app",
                        "entry_count": len(entries),
                        "highest_level": highest_level,
                        "received_at": received_at,
                    },
                )

        if "sms" in allowed_channels:
            sms_sent, sms_error = _send_transition_sms(recipient=recipient, message=sms_body)
            if sms_sent:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_cloud_log_sms",
                    summary=f"Cloud-log alert for {resource.name} sent by SMS.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "sms",
                        "entry_count": len(entries),
                        "highest_level": highest_level,
                    },
                )
            elif sms_error:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_cloud_log_sms_failed",
                    summary=f"Cloud-log alert SMS failed for {resource.name}.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "sms",
                        "error": sms_error,
                    },
                )

        if "email" in allowed_channels:
            email_sent, email_error = _send_transition_email(recipient=recipient, subject=subject, message=body)
            if email_sent:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_cloud_log_email",
                    summary=f"Cloud-log alert for {resource.name} sent by email.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "email",
                        "entry_count": len(entries),
                        "highest_level": highest_level,
                    },
                )
            elif email_error:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_cloud_log_email_failed",
                    summary=f"Cloud-log alert email failed for {resource.name}.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "email",
                        "error": email_error,
                    },
                )

    return dispatched


def _dispatch_health_transition_alerts(
    *,
    user,
    resource,
    previous_status: str,
    current_status: str,
    checked_at: str,
    check_method: str,
    target: str,
    error: str,
    latency_ms: float | None,
    packet_loss_pct: float | None,
) -> None:
    previous = str(previous_status or "").strip().lower()
    current = str(current_status or "").strip().lower()
    if previous not in {STATUS_HEALTHY, STATUS_UNHEALTHY}:
        return
    if current not in {STATUS_HEALTHY, STATUS_UNHEALTHY}:
        return
    if previous == current:
        return

    is_recovery = previous == STATUS_UNHEALTHY and current == STATUS_HEALTHY
    event_label = "health_recovered" if is_recovery else "health_degraded"
    subject = (
        f"[Alshival] Resource recovered: {resource.name}"
        if is_recovery
        else f"[Alshival] Resource unhealthy: {resource.name}"
    )
    body_lines = [
        f"Resource: {resource.name}",
        f"UUID: {resource.resource_uuid}",
        f"Status transition: {previous} -> {current}",
        f"Checked at: {checked_at}",
        f"Target: {target}",
        f"Check method: {check_method}",
    ]
    if latency_ms is not None:
        body_lines.append(f"Latency (ms): {latency_ms}")
    if packet_loss_pct is not None:
        body_lines.append(f"Packet loss (%): {packet_loss_pct}")
    if error:
        body_lines.append(f"Error: {error}")
    body = "\n".join(body_lines).strip()
    sms_body = (
        f"{resource.name}: {previous}->{current}. "
        f"target={target} method={check_method}"
        + (f" err={error}" if error else "")
    )

    recipients = _resource_alert_recipients(user, resource.resource_uuid)
    for recipient in recipients:
        recipient_id = int(getattr(recipient, "id", 0) or 0)
        if recipient_id <= 0:
            continue
        settings_payload = get_resource_alert_settings(user, resource.resource_uuid, recipient_id)
        candidate_channels: list[str] = []
        if bool(settings_payload.get("health_alerts_app_enabled", True)):
            candidate_channels.append("app")
        if bool(settings_payload.get("health_alerts_sms_enabled", False)):
            candidate_channels.append("sms")
        if bool(settings_payload.get("health_alerts_email_enabled", False)):
            candidate_channels.append("email")
        allowed_channels = _alert_filter_allowed_channels(
            recipient=recipient,
            alert_kind="health_transition",
            candidate_channels=candidate_channels,
            subject=subject,
            body=body,
            context={
                "resource_uuid": str(resource.resource_uuid or "").strip(),
                "resource_name": str(resource.name or "").strip(),
                "previous_status": previous,
                "current_status": current,
                "checked_at": checked_at,
                "check_method": check_method,
                "target": target,
                "error": error,
                "latency_ms": latency_ms,
                "packet_loss_pct": packet_loss_pct,
            },
        )
        if not allowed_channels:
            continue

        if "app" in allowed_channels:
            notification_id = add_user_notification(
                recipient,
                kind=event_label,
                title=subject,
                body=body,
                resource_uuid=str(resource.resource_uuid or "").strip(),
                level="info" if is_recovery else "warning",
                channel="app",
                metadata={
                    "source": "run_resource_health_worker",
                    "recipient_user_id": recipient_id,
                    "recipient_username": str(getattr(recipient, "username", "") or ""),
                    "check_method": check_method,
                    "target": target,
                    "previous_status": previous,
                    "current_status": current,
                    "error": error,
                    "latency_ms": latency_ms,
                    "packet_loss_pct": packet_loss_pct,
                    "checked_at": checked_at,
                },
            )
            if notification_id:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_health_app",
                    summary=f"Resource health alert for {resource.name} delivered in app.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "app",
                        "previous_status": previous,
                        "current_status": current,
                        "checked_at": checked_at,
                    },
                )

        if "sms" in allowed_channels:
            sms_sent, sms_error = _send_transition_sms(recipient=recipient, message=sms_body)
            if sms_sent:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_health_sms",
                    summary=f"Resource health alert for {resource.name} sent by SMS.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "sms",
                        "previous_status": previous,
                        "current_status": current,
                        "checked_at": checked_at,
                    },
                )
            elif sms_error:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_health_sms_failed",
                    summary=f"Resource health alert SMS failed for {resource.name}.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "sms",
                        "error": sms_error,
                    },
                )

        if "email" in allowed_channels:
            email_sent, email_error = _send_transition_email(recipient=recipient, subject=subject, message=body)
            if email_sent:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_health_email",
                    summary=f"Resource health alert for {resource.name} sent by email.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "email",
                        "previous_status": previous,
                        "current_status": current,
                        "checked_at": checked_at,
                    },
                )
            elif email_error:
                _log_alert_context_event_safe(
                    recipient=recipient,
                    event_type="alert_health_email_failed",
                    summary=f"Resource health alert email failed for {resource.name}.",
                    payload={
                        "resource_uuid": str(resource.resource_uuid or "").strip(),
                        "resource_name": str(resource.name or "").strip(),
                        "channel": "email",
                        "error": email_error,
                    },
                )


def _http_healthcheck(url: str, timeout: float) -> tuple[str, str]:
    try:
        req = Request(url, headers={"User-Agent": "AlshivalHealth/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None)
            if code is None and hasattr(resp, "getcode"):
                code = resp.getcode()
        if code is None:
            return STATUS_UNKNOWN, "No HTTP status code"
        if 200 <= int(code) < 400:
            return STATUS_HEALTHY, ""
        return STATUS_UNHEALTHY, f"HTTP {code}"
    except HTTPError as exc:
        return STATUS_UNHEALTHY, f"HTTP {exc.code}"
    except Exception as exc:
        return STATUS_UNHEALTHY, str(exc)


def _socket_check(address: str, port: int, timeout: float) -> tuple[str, str]:
    try:
        with socket.create_connection((address, int(port)), timeout=timeout):
            return STATUS_HEALTHY, ""
    except Exception as exc:
        return STATUS_UNHEALTHY, str(exc)


def _target_from_resource(resource) -> str:
    if resource.healthcheck_url:
        return resource.healthcheck_url
    if resource.resource_type == "vm" and resource.address:
        return resource.address
    if resource.address and resource.port:
        return f"{resource.address}:{resource.port}"
    if resource.address:
        return resource.address
    return resource.target or "unknown"


def _coerce_port(value: str | None, default: int) -> int:
    try:
        if value:
            parsed = int(value)
            if 1 <= parsed <= 65535:
                return parsed
    except Exception:
        pass
    return int(default)


def _parse_ping_metrics(output: str) -> tuple[float | None, float | None]:
    text = str(output or "")
    latency_match = re.search(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms", text, flags=re.IGNORECASE)
    loss_match = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s*packet loss", text, flags=re.IGNORECASE)

    latency_ms = None
    packet_loss_pct = None
    if latency_match:
        try:
            latency_ms = float(latency_match.group(1))
        except Exception:
            latency_ms = None
    if loss_match:
        try:
            packet_loss_pct = float(loss_match.group(1))
        except Exception:
            packet_loss_pct = None
    return latency_ms, packet_loss_pct


def _ping_check(address: str, timeout_seconds: int = 3) -> tuple[str, str, float | None, float | None]:
    ping_binary = which("ping") or "ping"
    command = [ping_binary, "-n", "-c", "1", "-W", str(timeout_seconds), address]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 2,
            check=False,
        )
        combined_output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        latency_ms, packet_loss_pct = _parse_ping_metrics(combined_output)
        if completed.returncode == 0:
            return STATUS_HEALTHY, "", latency_ms, packet_loss_pct
        error = (completed.stderr or completed.stdout or f"ping exited with {completed.returncode}").strip()
        return STATUS_UNHEALTHY, error, latency_ms, packet_loss_pct
    except FileNotFoundError:
        return STATUS_UNHEALTHY, "ping binary not found on host", None, None
    except subprocess.TimeoutExpired:
        return STATUS_UNHEALTHY, "ping timeout", None, None
    except Exception as exc:
        return STATUS_UNHEALTHY, str(exc), None, None


def _resource_has_ssh_config(resource) -> bool:
    ssh_username = str(getattr(resource, "ssh_username", "") or "").strip()
    ssh_key_name = str(getattr(resource, "ssh_key_name", "") or "").strip()
    ssh_credential_id = str(getattr(resource, "ssh_credential_id", "") or "").strip()
    ssh_key_present = bool(getattr(resource, "ssh_key_present", False))
    return bool(ssh_username and (ssh_key_present or ssh_credential_id or ssh_key_name))


def _ping_address_from_resource(resource) -> str:
    if resource.address:
        return str(resource.address).strip()
    if resource.target:
        raw_target = str(resource.target).strip()
        parsed = urlparse(raw_target)
        if parsed.hostname:
            return parsed.hostname
        if ":" in raw_target:
            host, _ = raw_target.rsplit(":", 1)
            return host.strip()
    return ""


def _fallback_check(resource) -> tuple[str, str, str]:
    if resource.healthcheck_url:
        status, error = _http_healthcheck(resource.healthcheck_url, timeout=6)
        return status, error, "http"

    if resource.resource_type == "vm" and resource.address:
        # VM endpoints commonly disable ICMP; allow TCP/SSH reachability as fallback.
        ssh_port = _coerce_port(resource.ssh_port, 22)
        status, error = _socket_check(resource.address, ssh_port, timeout=4)
        return status, error, "ssh"

    if resource.address and resource.port:
        status, error = _socket_check(resource.address, _coerce_port(resource.port, 80), timeout=4)
        return status, error, "tcp"

    if resource.target:
        parsed = urlparse(resource.target)
        if parsed.scheme in ("http", "https"):
            status, error = _http_healthcheck(resource.target, timeout=6)
            return status, error, "http"
        if ":" in resource.target:
            host, port_str = resource.target.rsplit(":", 1)
            status, error = _socket_check(host, _coerce_port(port_str, 80), timeout=4)
            return status, error, "tcp"

    if resource.address:
        return STATUS_UNKNOWN, "No service fallback configured for host-only target", "fallback"

    return STATUS_UNKNOWN, "No fallback target configured", "fallback"


def _check_resource(resource) -> tuple[str, str, str, float | None, float | None]:
    ping_target = _ping_address_from_resource(resource)
    if ping_target:
        ping_status, ping_error, latency_ms, packet_loss_pct = _ping_check(ping_target)
        if ping_status == STATUS_HEALTHY:
            return ping_status, "", "ping", latency_ms, packet_loss_pct
        fallback_status, fallback_error, fallback_method = _fallback_check(resource)
        if fallback_status == STATUS_HEALTHY:
            msg = f"Ping failed: {ping_error}; fallback {fallback_method} succeeded"
            return STATUS_HEALTHY, msg, f"ping+{fallback_method}", latency_ms, packet_loss_pct
        msg = f"Ping failed: {ping_error}; fallback {fallback_method} failed: {fallback_error}"
        return STATUS_UNHEALTHY, msg, f"ping+{fallback_method}", latency_ms, packet_loss_pct

    fallback_status, fallback_error, fallback_method = _fallback_check(resource)
    return fallback_status, fallback_error, fallback_method, None, None


def probe_resource_ping(resource) -> tuple[str, str, str, float | None, float | None]:
    ping_target = _ping_address_from_resource(resource)
    if not ping_target:
        return STATUS_UNKNOWN, "No ping target configured", "", None, None
    status, error, latency_ms, packet_loss_pct = _ping_check(ping_target)
    return status, error, ping_target, latency_ms, packet_loss_pct


def _log_health_transition(
    *,
    user,
    resource,
    previous_status: str,
    current_status: str,
    checked_at: str,
    check_method: str,
    target: str,
    error: str,
    latency_ms: float | None,
    packet_loss_pct: float | None,
) -> None:
    previous = str(previous_status or "").strip().lower()
    current = str(current_status or "").strip().lower()
    if previous not in {STATUS_HEALTHY, STATUS_UNHEALTHY}:
        return
    if current not in {STATUS_HEALTHY, STATUS_UNHEALTHY}:
        return
    if previous == current:
        return

    is_recovery = previous == STATUS_UNHEALTHY and current == STATUS_HEALTHY
    level = "info" if is_recovery else "warning"
    message = (
        f"Health status changed: {previous} -> {current}"
        if not is_recovery
        else f"Health status recovered: {previous} -> {current}"
    )
    store_resource_logs(
        user,
        resource.resource_uuid,
        {
            "resource_id": resource.resource_uuid,
            "resource_uuid": resource.resource_uuid,
            "submitted_by_username": "resource-monitor",
            "received_at": checked_at,
            "logs": [
                {
                    "level": level,
                    "logger": "resource_monitor",
                    "message": message,
                    "ts": checked_at,
                    "extra": {
                        "source": "run_resource_health_worker",
                        "check_method": check_method,
                        "target": target,
                        "previous_status": previous,
                        "current_status": current,
                        "error": error,
                        "latency_ms": latency_ms,
                        "packet_loss_pct": packet_loss_pct,
                    },
                }
            ],
        },
        ip_address=None,
        user_agent="resource-monitor",
    )


def check_health(resource_id: int, user=None, *, emit_transition_log: bool = False) -> HealthResult:
    current_user = user or get_current_user()
    if current_user is None:
        raise RuntimeError("check_health requires a user for multi-tenant lookups.")

    resource = get_resource(current_user, resource_id)
    if resource is None:
        raise ValueError(f"Resource {resource_id} not found for user.")
    if not is_global_monitoring_enabled():
        now = datetime.now(timezone.utc).isoformat()
        return HealthResult(
            resource_id=resource_id,
            status=STATUS_UNKNOWN,
            checked_at=now,
            target=_target_from_resource(resource),
            error="Global resource monitoring is disabled by an administrator.",
            check_method="disabled",
            latency_ms=None,
            packet_loss_pct=None,
        )
    previous_status = str(resource.last_status or "").strip().lower()

    status, error, check_method, latency_ms, packet_loss_pct = _check_resource(resource)
    persisted_error = error if status != STATUS_HEALTHY else ""
    checked_at = datetime.now(timezone.utc).isoformat()
    target = _target_from_resource(resource)
    update_resource_health(current_user, resource_id, status, checked_at, persisted_error)
    log_resource_check(
        current_user,
        resource_id,
        status,
        checked_at,
        target,
        persisted_error,
        resource_uuid=resource.resource_uuid,
        check_method=check_method,
        latency_ms=latency_ms,
        packet_loss_pct=packet_loss_pct,
    )
    if emit_transition_log:
        try:
            _log_health_transition(
                user=current_user,
                resource=resource,
                previous_status=previous_status,
                current_status=status,
                checked_at=checked_at,
                check_method=check_method,
                target=target,
                error=persisted_error,
                latency_ms=latency_ms,
                packet_loss_pct=packet_loss_pct,
            )
        except Exception:
            pass
        try:
            _dispatch_health_transition_alerts(
                user=current_user,
                resource=resource,
                previous_status=previous_status,
                current_status=status,
                checked_at=checked_at,
                check_method=check_method,
                target=target,
                error=persisted_error,
                latency_ms=latency_ms,
                packet_loss_pct=packet_loss_pct,
            )
        except Exception:
            pass
    try:
        upsert_resource_health_knowledge(
            user=current_user,
            resource=resource,
            status=status,
            checked_at=checked_at,
            error=persisted_error,
            check_method=check_method,
            latency_ms=latency_ms,
            packet_loss_pct=packet_loss_pct,
        )
    except Exception:
        pass
    return HealthResult(
        resource_id=resource_id,
        status=status,
        checked_at=checked_at,
        target=target,
        error=persisted_error,
        check_method=check_method,
        latency_ms=latency_ms,
        packet_loss_pct=packet_loss_pct,
    )
