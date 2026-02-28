from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse

from .models import UserNotificationSettings, WikiPage
from .resources_store import (
    add_ask_chat_context_event,
    add_user_notification,
    get_resource_by_uuid,
    list_due_reminders,
    update_reminder,
)
from .support_inbox import send_support_inbox_email


def _normalize_phone(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    keep_plus = raw.startswith("+")
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""
    return f"+{digits}" if keep_plus else digits


def _twilio_sms_credentials() -> tuple[str, str, str]:
    from .setup_state import get_setup_state

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


def _send_twilio_sms(*, recipient, message: str) -> tuple[bool, str, str]:
    from .setup_state import is_twilio_configured

    if not is_twilio_configured():
        return False, "twilio_not_configured", ""

    account_sid, auth_token, from_number = _twilio_sms_credentials()
    if not (account_sid and auth_token and from_number):
        return False, "twilio_not_configured", ""

    phone_raw = (
        UserNotificationSettings.objects.filter(user=recipient)
        .values_list("phone_number", flat=True)
        .first()
        or ""
    )
    to_number = _normalize_phone(phone_raw)
    if not to_number:
        return False, "missing_phone_number", ""

    body = str(message or "").strip()
    if not body:
        return False, "sms_body_required", to_number
    if len(body) > 1200:
        body = body[:1200]

    try:
        response = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            data={
                "To": to_number,
                "From": from_number,
                "Body": body,
            },
            auth=(account_sid, auth_token),
            timeout=10,
        )
    except requests.RequestException as exc:
        return False, f"twilio_request_failed:{exc}", to_number

    if 200 <= int(response.status_code) < 300:
        return True, "", to_number
    return False, f"twilio_status_{int(response.status_code)}", to_number


def _actor_can_contact_user(*, actor, target_user) -> bool:
    if actor is None or target_user is None:
        return False
    if bool(getattr(actor, "is_superuser", False)):
        return True
    actor_id = int(getattr(actor, "id", 0) or 0)
    target_id = int(getattr(target_user, "id", 0) or 0)
    if actor_id > 0 and actor_id == target_id:
        return True
    actor_team_ids = set(actor.groups.values_list("id", flat=True))
    if not actor_team_ids:
        return False
    target_team_ids = set(target_user.groups.values_list("id", flat=True))
    return bool(actor_team_ids & target_team_ids)


def _normalize_recipients(recipients: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in recipients or []:
        username = str(raw or "").strip().lstrip("@").lower()
        if not username or username in seen:
            continue
        normalized.append(username)
        seen.add(username)
    return normalized


def _resolve_reminder_recipients(owner_user, recipients: list[str] | tuple[str, ...] | None) -> tuple[list[object], list[dict[str, str]]]:
    if owner_user is None:
        return [], [{"username": "", "reason": "owner_missing"}]

    owner_username = str(getattr(owner_user, "username", "") or "").strip().lower()
    requested = _normalize_recipients(recipients)
    if not requested and owner_username:
        requested = [owner_username]

    User = get_user_model()
    lookup_usernames = [item for item in requested if item != owner_username]
    matches: list[Any] = []
    if lookup_usernames:
        query = Q()
        for username in lookup_usernames:
            query |= Q(username__iexact=username)
        matches = list(User.objects.filter(query, is_active=True).order_by("id"))

    by_username = {
        str(getattr(item, "username", "") or "").strip().lower(): item
        for item in matches
        if str(getattr(item, "username", "") or "").strip()
    }

    valid_users: list[Any] = []
    invalid: list[dict[str, str]] = []
    for username in requested:
        if username == owner_username:
            valid_users.append(owner_user)
            continue
        target_user = by_username.get(username)
        if target_user is None:
            invalid.append({"username": username, "reason": "not_found"})
            continue
        if not _actor_can_contact_user(actor=owner_user, target_user=target_user):
            invalid.append({"username": username, "reason": "outside_team_scope"})
            continue
        valid_users.append(target_user)

    deduped: list[Any] = []
    seen_ids: set[int] = set()
    for user in valid_users:
        user_id = int(getattr(user, "id", 0) or 0)
        if user_id <= 0 or user_id in seen_ids:
            continue
        seen_ids.add(user_id)
        deduped.append(user)
    return deduped, invalid


def _absolute_app_url(path: str) -> str:
    relative = str(path or "").strip()
    if not relative:
        return ""
    if relative.startswith("http://") or relative.startswith("https://"):
        return relative
    if not relative.startswith("/"):
        relative = "/" + relative
    base = str(getattr(settings, "APP_BASE_URL", "") or "").strip().rstrip("/")
    return f"{base}{relative}" if base else relative


def _extract_resource_uuids(reminder: dict[str, Any]) -> list[str]:
    metadata = reminder.get("metadata") if isinstance(reminder.get("metadata"), dict) else {}
    candidates: list[str] = []

    direct = str(metadata.get("resource_uuid") or "").strip()
    if direct:
        candidates.append(direct)

    many = metadata.get("resource_uuids")
    if isinstance(many, list):
        for item in many:
            value = str(item or "").strip()
            if value:
                candidates.append(value)

    title = str(reminder.get("title") or "")
    message = str(reminder.get("message") or "")
    pattern = re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    )
    candidates.extend(pattern.findall(f"{title}\n{message}"))

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        lowered = str(raw or "").strip().lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(lowered)
    return cleaned[:6]


def _resource_dossier_entries(owner_user, reminder: dict[str, Any]) -> list[dict[str, str]]:
    owner_username = str(getattr(owner_user, "username", "") or "").strip()
    entries: list[dict[str, str]] = []
    for resource_uuid in _extract_resource_uuids(reminder):
        resource = get_resource_by_uuid(owner_user, resource_uuid)
        if resource is None:
            continue
        name = str(getattr(resource, "name", "") or "").strip() or resource_uuid
        route = f"/u/{owner_username}/resources/{resource_uuid}/" if owner_username else ""
        entries.append(
            {
                "resource_uuid": resource_uuid,
                "resource_name": name,
                "url": _absolute_app_url(route) if route else "",
            }
        )
        if len(entries) >= 4:
            break
    return entries


def _page_visible_to_user(*, user, page: WikiPage) -> bool:
    if bool(getattr(user, "is_superuser", False)):
        return True
    if page.scope == WikiPage.SCOPE_RESOURCE and str(page.resource_uuid or "").strip():
        from .request_auth import user_can_access_resource

        return bool(user_can_access_resource(user=user, resource_uuid=str(page.resource_uuid or "").strip()))
    team_ids = list(page.team_access.values_list("id", flat=True))
    if not team_ids:
        return True
    return bool(user.groups.filter(id__in=team_ids).exists())


def _wiki_dossier_entries(*, recipient_user, reminder: dict[str, Any]) -> list[dict[str, str]]:
    title = str(reminder.get("title") or "").strip()
    message = str(reminder.get("message") or "").strip()
    metadata = reminder.get("metadata") if isinstance(reminder.get("metadata"), dict) else {}
    hints = str(metadata.get("wiki_query") or "").strip()

    terms = [token.lower() for token in re.findall(r"[A-Za-z0-9_\-]{4,}", f"{title} {message} {hints}")]
    dedup_terms: list[str] = []
    seen_terms: set[str] = set()
    for item in terms:
        if item in seen_terms:
            continue
        seen_terms.add(item)
        dedup_terms.append(item)
    dedup_terms = dedup_terms[:5]

    query = WikiPage.objects.filter(
        is_draft=False,
        scope=WikiPage.SCOPE_WORKSPACE,
    ).order_by("-updated_at")
    if dedup_terms:
        term_query = Q()
        for term in dedup_terms:
            term_query |= Q(title__icontains=term) | Q(path__icontains=term)
        query = query.filter(term_query)

    rows = list(query[:20])
    entries: list[dict[str, str]] = []
    for page in rows:
        if not _page_visible_to_user(user=recipient_user, page=page):
            continue
        path = str(page.path or "").strip()
        if not path:
            continue
        link = reverse("wiki")
        link = f"{link}?{urlencode({'path': path})}"
        entries.append(
            {
                "title": str(page.title or "").strip() or path,
                "path": path,
                "url": _absolute_app_url(link),
            }
        )
        if len(entries) >= 4:
            break

    if not entries:
        entries.append(
            {
                "title": "Workspace Wiki",
                "path": "",
                "url": _absolute_app_url(reverse("wiki")),
            }
        )
    return entries


def _fallback_channel_message(*, channel: str, owner_user, recipient_user, reminder: dict[str, Any], dossier: dict[str, Any]) -> str:
    title = str(reminder.get("title") or "").strip() or "Reminder"
    note = str(reminder.get("message") or "").strip()
    remind_at = str(reminder.get("remind_at") or "").strip()
    owner_username = str(getattr(owner_user, "username", "") or "").strip()
    recipient_username = str(getattr(recipient_user, "username", "") or "").strip()

    if channel == "sms":
        lead = f"Reminder from @{owner_username}: {title}" if recipient_username != owner_username else f"Reminder: {title}"
        text = f"{lead}. {note}".strip()
        return re.sub(r"\s+", " ", text)[:300]

    lines = [
        f"Reminder: {title}",
        f"Due at: {remind_at}" if remind_at else "",
        note,
    ]
    resources = dossier.get("resources") if isinstance(dossier.get("resources"), list) else []
    if resources:
        lines.append("")
        lines.append("Related resources:")
        for item in resources[:3]:
            name = str(item.get("resource_name") or item.get("resource_uuid") or "Resource").strip()
            link = str(item.get("url") or "").strip()
            lines.append(f"- {name}{(' | ' + link) if link else ''}")
    wiki_links = dossier.get("wiki_links") if isinstance(dossier.get("wiki_links"), list) else []
    if wiki_links:
        lines.append("")
        lines.append("Wiki links:")
        for item in wiki_links[:3]:
            title_text = str(item.get("title") or item.get("path") or "Wiki page").strip()
            link = str(item.get("url") or "").strip()
            lines.append(f"- {title_text}{(' | ' + link) if link else ''}")
    return "\n".join(item for item in lines if item)


def _extract_chat_completion_text(payload: dict[str, Any] | None) -> str:
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


def _openai_config() -> tuple[str, str]:
    from .setup_state import get_setup_state

    setup = get_setup_state()
    api_key = str(getattr(setup, "openai_api_key", "") or "").strip() if setup else ""
    if not api_key:
        api_key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()
    model = (
        str(getattr(settings, "ALSHIVAL_OPENAI_CHAT_MODEL", "") or "").strip()
        or str(getattr(setup, "default_model", "") or "").strip()
        or "gpt-4.1-mini"
    )
    return api_key, model


def _generate_channel_message_with_agent(*, channel: str, owner_user, recipient_user, reminder: dict[str, Any], dossier: dict[str, Any]) -> tuple[str, str]:
    fallback = _fallback_channel_message(
        channel=channel,
        owner_user=owner_user,
        recipient_user=recipient_user,
        reminder=reminder,
        dossier=dossier,
    )

    api_key, model = _openai_config()
    if not api_key:
        return fallback, "openai_not_configured"

    system_lines = [
        "You are Alshival and your only job is to draft one final reminder message.",
        "Output plain text only, no markdown.",
    ]
    if channel == "sms":
        system_lines.extend(
            [
                "The message is for SMS.",
                "Keep it short and clear.",
                "Max 280 characters.",
                "No bullet lists.",
            ]
        )
    elif channel == "email":
        system_lines.extend(
            [
                "The message is for email from a shared support inbox.",
                "You may include concise helpful context and links when provided.",
                "Keep it practical and concise.",
                "Use short sections when helpful.",
                "Max 1800 characters.",
            ]
        )
    else:
        system_lines.extend(
            [
                "The message is for an in-app notification.",
                "Keep it concise.",
                "Max 900 characters.",
            ]
        )

    payload = {
        "channel": channel,
        "owner": {
            "user_id": int(getattr(owner_user, "id", 0) or 0),
            "username": str(getattr(owner_user, "username", "") or "").strip(),
            "email": str(getattr(owner_user, "email", "") or "").strip().lower(),
        },
        "recipient": {
            "user_id": int(getattr(recipient_user, "id", 0) or 0),
            "username": str(getattr(recipient_user, "username", "") or "").strip(),
            "email": str(getattr(recipient_user, "email", "") or "").strip().lower(),
        },
        "reminder": {
            "id": int(reminder.get("id") or 0),
            "title": str(reminder.get("title") or "").strip(),
            "message": str(reminder.get("message") or "").strip(),
            "remind_at": str(reminder.get("remind_at") or "").strip(),
        },
        "dossier": dossier,
    }

    request_payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "\n".join(system_lines)},
            {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
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
    except requests.RequestException as exc:
        return fallback, f"openai_unreachable:{exc}"

    if int(response.status_code) >= 400:
        return fallback, f"openai_status_{int(response.status_code)}"

    data = response.json() if response.content else {}
    text = _extract_chat_completion_text(data)
    if not text:
        return fallback, "openai_empty_response"

    cleaned = str(text).strip()
    if channel == "sms":
        cleaned = re.sub(r"\s+", " ", cleaned)[:300]
    elif channel == "email":
        cleaned = cleaned[:1800]
    else:
        cleaned = cleaned[:900]

    if not cleaned:
        return fallback, "openai_empty_response"
    return cleaned, ""


def _log_context_event_safe(*, user, event_type: str, summary: str, payload: dict[str, Any]) -> None:
    try:
        add_ask_chat_context_event(
            user,
            event_type=event_type,
            summary=summary,
            payload=payload,
            conversation_id="default",
        )
    except Exception:
        return


def _deliver_reminder_to_recipient(*, owner_user, recipient_user, reminder: dict[str, Any], dry_run: bool) -> tuple[bool, dict[str, bool], list[str]]:
    from .setup_state import is_support_inbox_email_alerts_enabled

    channels = reminder.get("channels") if isinstance(reminder.get("channels"), dict) else {}
    flags = {
        "APP": bool(channels.get("APP", True)),
        "SMS": bool(channels.get("SMS", True)),
        "EMAIL": bool(channels.get("EMAIL", False)),
    }

    title = str(reminder.get("title") or "").strip() or "Reminder"
    remind_at = str(reminder.get("remind_at") or "").strip()
    owner_username = str(getattr(owner_user, "username", "") or "").strip()
    recipient_username = str(getattr(recipient_user, "username", "") or "").strip()
    dossier = {
        "resources": _resource_dossier_entries(owner_user, reminder),
        "wiki_links": _wiki_dossier_entries(recipient_user=recipient_user, reminder=reminder),
    }

    deliveries = {"APP": False, "SMS": False, "EMAIL": False}
    errors: list[str] = []

    if flags["APP"]:
        app_message, app_error = _generate_channel_message_with_agent(
            channel="app",
            owner_user=owner_user,
            recipient_user=recipient_user,
            reminder=reminder,
            dossier=dossier,
        )
        if dry_run:
            deliveries["APP"] = True
        else:
            notification_id = add_user_notification(
                recipient_user,
                kind="reminder_due",
                title=f"Reminder: {title}",
                body=app_message,
                level="info",
                channel="app",
                metadata={
                    "source": "reminder_worker",
                    "reminder_id": int(reminder.get("id") or 0),
                    "owner_user_id": int(getattr(owner_user, "id", 0) or 0),
                    "owner_username": owner_username,
                    "recipient_user_id": int(getattr(recipient_user, "id", 0) or 0),
                    "recipient_username": recipient_username,
                    "remind_at": remind_at,
                    "generator_error": app_error,
                },
            )
            deliveries["APP"] = bool(notification_id)
            if deliveries["APP"]:
                _log_context_event_safe(
                    user=recipient_user,
                    event_type="reminder_sent_app",
                    summary=f"Reminder '{title}' delivered in app from @{owner_username}.",
                    payload={
                        "reminder_id": int(reminder.get("id") or 0),
                        "channel": "app",
                        "owner_username": owner_username,
                        "recipient_username": recipient_username,
                        "remind_at": remind_at,
                    },
                )
        if not deliveries["APP"]:
            errors.append("app_notification_failed")

    if flags["SMS"]:
        sms_message, sms_error = _generate_channel_message_with_agent(
            channel="sms",
            owner_user=owner_user,
            recipient_user=recipient_user,
            reminder=reminder,
            dossier=dossier,
        )
        if dry_run:
            deliveries["SMS"] = True
        else:
            sms_sent, sms_send_error, to_number = _send_twilio_sms(recipient=recipient_user, message=sms_message)
            deliveries["SMS"] = bool(sms_sent)
            if sms_sent:
                _log_context_event_safe(
                    user=recipient_user,
                    event_type="reminder_sent_sms",
                    summary=f"Reminder '{title}' sent by SMS from @{owner_username}.",
                    payload={
                        "reminder_id": int(reminder.get("id") or 0),
                        "channel": "sms",
                        "owner_username": owner_username,
                        "recipient_username": recipient_username,
                        "to_number": to_number,
                        "remind_at": remind_at,
                        "generator_error": sms_error,
                    },
                )
            else:
                errors.append(sms_send_error or "sms_send_failed")
        if not deliveries["SMS"] and dry_run:
            errors.append("sms_dry_run_unexpected")

    if flags["EMAIL"]:
        if not is_support_inbox_email_alerts_enabled():
            errors.append("support_inbox_email_disabled")
        else:
            email_message, email_error = _generate_channel_message_with_agent(
                channel="email",
                owner_user=owner_user,
                recipient_user=recipient_user,
                reminder=reminder,
                dossier=dossier,
            )
            recipient_email = str(getattr(recipient_user, "email", "") or "").strip().lower()
            if not recipient_email:
                errors.append("email_missing")
            elif dry_run:
                deliveries["EMAIL"] = True
            else:
                sent, send_error = send_support_inbox_email(
                    recipient_email=recipient_email,
                    subject=f"[Alshival] Reminder: {title}",
                    body_text=email_message,
                    initiated_by_user_id=int(getattr(owner_user, "id", 0) or 0),
                    initiated_by_username=owner_username,
                    initiated_by_email=str(getattr(owner_user, "email", "") or "").strip().lower(),
                    initiated_by_channel="reminder_worker",
                    initiated_by_conversation_id=f"reminder-{int(reminder.get('id') or 0)}",
                )
                deliveries["EMAIL"] = bool(sent)
                if sent:
                    _log_context_event_safe(
                        user=recipient_user,
                        event_type="reminder_sent_email",
                        summary=f"Reminder '{title}' sent by email from @{owner_username}.",
                        payload={
                            "reminder_id": int(reminder.get("id") or 0),
                            "channel": "email",
                            "owner_username": owner_username,
                            "recipient_username": recipient_username,
                            "recipient_email": recipient_email,
                            "remind_at": remind_at,
                            "generator_error": email_error,
                            "resource_links": dossier.get("resources") or [],
                            "wiki_links": dossier.get("wiki_links") or [],
                        },
                    )
                else:
                    errors.append(send_error or "email_send_failed")

    return any(deliveries.values()), deliveries, errors


def process_due_reminders_for_owner(owner_user, *, per_user_limit: int = 200, dry_run: bool = False) -> dict[str, int]:
    due = list_due_reminders(
        owner_user,
        now_dt=datetime.now(timezone.utc),
        limit=max(1, int(per_user_limit)),
    )
    counters = {"due": len(due), "sent": 0, "error": 0, "skipped": 0}

    owner_username = str(getattr(owner_user, "username", "") or "").strip()

    for reminder in due:
        reminder_id = int(reminder.get("id") or 0)
        if reminder_id <= 0:
            counters["skipped"] += 1
            continue

        action = str(reminder.get("action") or "").strip().lower()
        if action not in {"notify_user", "notify", "notify_collaborators"}:
            status = "error"
            error = f"unsupported_action:{action or 'unknown'}"
            if not dry_run:
                update_reminder(owner_user, reminder_id, status=status, last_error=error)
            counters["error"] += 1
            continue

        recipients = reminder.get("recipients")
        if not isinstance(recipients, list):
            recipients = []
        valid_users, invalid_recipients = _resolve_reminder_recipients(owner_user, recipients)

        successful_recipients: list[str] = []
        delivery_failures: list[dict[str, Any]] = []
        for recipient_user in valid_users:
            delivered, channel_results, errors = _deliver_reminder_to_recipient(
                owner_user=owner_user,
                recipient_user=recipient_user,
                reminder=reminder,
                dry_run=dry_run,
            )
            recipient_username = str(getattr(recipient_user, "username", "") or "").strip().lower()
            if delivered:
                successful_recipients.append(recipient_username)
            else:
                delivery_failures.append(
                    {
                        "username": recipient_username,
                        "channels": channel_results,
                        "errors": errors,
                    }
                )

        status = "sent" if successful_recipients else "error"
        error_parts: list[str] = []
        if invalid_recipients:
            invalid_text = ",".join(
                f"@{item['username']}:{item['reason']}"
                for item in invalid_recipients
                if str(item.get("username") or "").strip()
            )
            if invalid_text:
                error_parts.append(f"invalid_recipients[{invalid_text}]")
        if delivery_failures:
            failed_text = ",".join(
                f"@{item.get('username') or 'unknown'}:{'|'.join(item.get('errors') or []) or 'no_channel_delivery'}"
                for item in delivery_failures
            )
            error_parts.append(f"delivery_failures[{failed_text}]")
        last_error = "; ".join(error_parts)[:2000]

        if not dry_run:
            update_reminder(
                owner_user,
                reminder_id,
                status=status,
                last_error=last_error,
            )

        _log_context_event_safe(
            user=owner_user,
            event_type="reminder_dispatched",
            summary=f"Reminder '{str(reminder.get('title') or reminder_id)}' processed ({status}).",
            payload={
                "reminder_id": reminder_id,
                "status": status,
                "owner_username": owner_username,
                "successful_recipients": successful_recipients,
                "invalid_recipients": invalid_recipients,
                "delivery_failures": delivery_failures,
                "dry_run": bool(dry_run),
            },
        )

        if status == "sent":
            counters["sent"] += 1
        else:
            counters["error"] += 1

    return counters


def run_due_reminders(
    *,
    user_filters: list[str] | None = None,
    per_user_limit: int = 200,
    dry_run: bool = False,
    max_users: int = 0,
) -> dict[str, int]:
    User = get_user_model()
    query = User.objects.filter(is_active=True).order_by("id")

    normalized_filters = {
        str(item or "").strip().lower()
        for item in (user_filters or [])
        if str(item or "").strip()
    }
    if normalized_filters:
        filter_query = Q()
        for item in normalized_filters:
            filter_query |= Q(username__iexact=item) | Q(email__iexact=item)
        query = query.filter(filter_query)

    users = list(query)
    if int(max_users or 0) > 0:
        users = users[: int(max_users)]

    totals = {
        "users": 0,
        "due": 0,
        "sent": 0,
        "error": 0,
        "skipped": 0,
    }
    for user in users:
        totals["users"] += 1
        counters = process_due_reminders_for_owner(
            user,
            per_user_limit=max(1, int(per_user_limit)),
            dry_run=bool(dry_run),
        )
        totals["due"] += int(counters.get("due", 0))
        totals["sent"] += int(counters.get("sent", 0))
        totals["error"] += int(counters.get("error", 0))
        totals["skipped"] += int(counters.get("skipped", 0))
    return totals
