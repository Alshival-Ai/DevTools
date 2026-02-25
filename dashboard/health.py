from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
from django.core.mail import send_mail

from dashboard.knowledge_store import upsert_resource_health_knowledge
from dashboard.models import ResourcePackageOwner, ResourceTeamShare, UserNotificationSettings
from dashboard.request_context import get_current_user
from dashboard.resources_store import (
    add_user_notification,
    get_resource_alert_settings,
    get_resource,
    log_resource_check,
    store_resource_logs,
    update_resource_health,
)
from dashboard.setup_state import (
    get_setup_state,
    is_email_provider_configured,
    is_global_monitoring_enabled,
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
    if not is_email_provider_configured():
        return False, "email_not_configured"
    recipient_email = str(getattr(recipient, "email", "") or "").strip()
    if not recipient_email:
        return False, "missing_email_address"
    from_email = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip() or "noreply@alshival.local"
    try:
        sent = send_mail(
            str(subject or "").strip()[:255],
            str(message or "").strip(),
            from_email,
            [recipient_email],
            fail_silently=False,
        )
    except Exception as exc:
        return False, f"email_send_failed:{exc}"
    return bool(int(sent or 0) > 0), ""


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

        if bool(settings_payload.get("cloud_log_errors_app_enabled", True)):
            add_user_notification(
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
            dispatched += 1

        if bool(settings_payload.get("cloud_log_errors_sms_enabled", False)):
            _send_transition_sms(recipient=recipient, message=sms_body)

        if bool(settings_payload.get("cloud_log_errors_email_enabled", False)):
            _send_transition_email(recipient=recipient, subject=subject, message=body)

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

        if bool(settings_payload.get("health_alerts_app_enabled", True)):
            add_user_notification(
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

        if bool(settings_payload.get("health_alerts_sms_enabled", False)):
            _send_transition_sms(recipient=recipient, message=sms_body)

        if bool(settings_payload.get("health_alerts_email_enabled", False)):
            _send_transition_email(recipient=recipient, subject=subject, message=body)


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

    if resource.resource_type == "vm" and resource.address and _resource_has_ssh_config(resource):
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
