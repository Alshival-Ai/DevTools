from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.db.utils import OperationalError, ProgrammingError

from .health import _alert_filter_allowed_channels, _send_transition_email, _send_transition_sms
from .resources_store import (
    add_ask_chat_context_event,
    add_user_notification,
    get_user_calendar_notification_settings,
    get_user_calendar_sync_state,
    list_user_calendar_event_cache,
    replace_user_calendar_event_cache,
    set_user_calendar_sync_state,
)


DEFAULT_CALENDAR_REFRESH_MIN_INTERVAL_SECONDS = 60
_MICROSOFT_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_MICROSOFT_GRAPH_TIMEOUT_SECONDS = 20
_MICROSOFT_CALENDAR_PAST_DAYS = 30
_MICROSOFT_CALENDAR_FUTURE_DAYS = 365
_MICROSOFT_CALENDAR_FETCH_LIMIT = 2000
_MICROSOFT_CALENDAR_PER_REQUEST_LIMIT = 200
_CALENDAR_ALERT_PREVIEW_LIMIT = 5


def _log_calendar_alert_context_event_safe(*, user, event_type: str, summary: str, payload: dict[str, Any]) -> None:
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


def _display_from_epoch(epoch: int) -> str:
    if int(epoch or 0) <= 0:
        return ""
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return ""


def _refresh_decision(*, state: dict[str, Any], now_epoch: int, force: bool, min_interval_seconds: int) -> tuple[bool, int | None, int]:
    last_epoch_raw = state.get("fetched_at_epoch", 0) if isinstance(state, dict) else 0
    try:
        last_epoch = int(last_epoch_raw or 0)
    except (TypeError, ValueError):
        last_epoch = 0
    age_seconds = (now_epoch - last_epoch) if last_epoch > 0 else None
    should_refresh = bool(force)
    if not should_refresh:
        should_refresh = bool(
            last_epoch <= 0
            or age_seconds is None
            or age_seconds >= int(min_interval_seconds),
        )
    return should_refresh, age_seconds, last_epoch


def _state_item_count(state: dict[str, Any]) -> int:
    try:
        return max(0, int((state or {}).get("item_count") or 0))
    except Exception:
        return 0


def _microsoft_access_token_for_user(user) -> tuple[bool, str, str]:
    try:
        account = (
            SocialAccount.objects.filter(user=user, provider="microsoft")
            .order_by("id")
            .first()
        )
    except (OperationalError, ProgrammingError):
        return False, "", ""
    except Exception:
        return False, "", "Unable to load Microsoft account connection."

    if account is None:
        return False, "", ""

    try:
        token_row = (
            SocialToken.objects.filter(account=account)
            .exclude(token__exact="")
            .order_by("-id")
            .first()
        )
    except (OperationalError, ProgrammingError):
        token_row = None
    except Exception:
        token_row = None

    access_token = str(getattr(token_row, "token", "") or "").strip()
    if access_token:
        return True, access_token, ""
    return True, "", "Microsoft is connected, but the OAuth token is missing. Reconnect Microsoft from Settings."


def _microsoft_graph_error(payload: dict[str, Any], status_code: int) -> str:
    if status_code in {401, 403}:
        return "Microsoft authorization expired. Reconnect Microsoft from Settings."
    error_obj = payload.get("error")
    if isinstance(error_obj, dict):
        message = str(error_obj.get("message") or "").strip()
        if message:
            return f"Microsoft Graph API error: {message}"
    return f"Microsoft Graph API error (HTTP {status_code})."


def _microsoft_graph_get_json(
    *,
    access_token: str,
    url: str,
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    token = str(access_token or "").strip()
    if not token:
        return None, "Microsoft token is not available."
    try:
        response = requests.get(
            str(url),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params=params or {},
            timeout=_MICROSOFT_GRAPH_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return None, "Unable to reach Microsoft Graph right now."

    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if int(response.status_code) >= 400:
        return None, _microsoft_graph_error(payload, int(response.status_code))
    return payload, ""


def _outlook_due_components(start_obj: dict[str, Any], *, is_all_day: bool) -> tuple[str, str]:
    if not isinstance(start_obj, dict):
        return "", ""
    raw_date_time = str(start_obj.get("dateTime") or "").strip()
    if not raw_date_time:
        return "", ""
    due_date = raw_date_time[:10] if len(raw_date_time) >= 10 else ""
    due_time = ""
    if not is_all_day and len(raw_date_time) >= 16:
        due_time = raw_date_time[11:16]
    if due_time and len(due_time) != 5:
        due_time = ""
    return due_date, due_time


def _fetch_outlook_events(access_token: str) -> tuple[list[dict[str, Any]], bool, str]:
    start_window = (datetime.now(timezone.utc) - timedelta(days=_MICROSOFT_CALENDAR_PAST_DAYS)).replace(microsecond=0)
    end_window = (datetime.now(timezone.utc) + timedelta(days=_MICROSOFT_CALENDAR_FUTURE_DAYS)).replace(microsecond=0)
    start_iso = start_window.isoformat().replace("+00:00", "Z")
    end_iso = end_window.isoformat().replace("+00:00", "Z")

    query_params: dict[str, Any] = {
        "startDateTime": start_iso,
        "endDateTime": end_iso,
        "$select": (
            "id,subject,start,end,isAllDay,isCancelled,webLink,lastModifiedDateTime,"
            "organizer,responseStatus,isOnlineMeeting,onlineMeetingProvider,onlineMeeting,onlineMeetingUrl"
        ),
        "$orderby": "start/dateTime",
        "$top": _MICROSOFT_CALENDAR_PER_REQUEST_LIMIT,
    }
    next_url = f"{_MICROSOFT_GRAPH_BASE_URL}/me/calendarView"
    next_params: dict[str, Any] | None = dict(query_params)
    truncated = False
    events: list[dict[str, Any]] = []

    while next_url:
        payload, error = _microsoft_graph_get_json(
            access_token=access_token,
            url=next_url,
            params=next_params,
        )
        if error:
            return [], truncated, error
        if payload is None:
            return [], truncated, "Unexpected empty response from Microsoft Graph."
        values = payload.get("value")
        if isinstance(values, list):
            for item in values:
                if not isinstance(item, dict):
                    continue
                events.append(item)
                if len(events) >= _MICROSOFT_CALENDAR_FETCH_LIMIT:
                    truncated = True
                    return events[:_MICROSOFT_CALENDAR_FETCH_LIMIT], truncated, ""
        next_link = str(payload.get("@odata.nextLink") or "").strip()
        if next_link:
            next_url = next_link
            next_params = None
        else:
            next_url = ""
            next_params = None

    return events, truncated, ""


def _outlook_calendar_cache_events(event_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in event_rows:
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("id") or "").strip()
        if not event_id:
            continue
        title = str(row.get("subject") or "").strip() or f"Outlook event {event_id}"
        is_all_day = bool(row.get("isAllDay"))
        is_cancelled = bool(row.get("isCancelled"))
        due_date, due_time = _outlook_due_components(
            row.get("start") if isinstance(row.get("start"), dict) else {},
            is_all_day=is_all_day,
        )
        response_status = row.get("responseStatus") if isinstance(row.get("responseStatus"), dict) else {}
        response_value = str(response_status.get("response") or "").strip().lower()
        status = "cancelled" if is_cancelled else "scheduled"
        if response_value in {"accepted", "tentativelyaccepted", "declined"}:
            status = response_value

        organizer_obj = row.get("organizer") if isinstance(row.get("organizer"), dict) else {}
        organizer_email_obj = organizer_obj.get("emailAddress") if isinstance(organizer_obj.get("emailAddress"), dict) else {}
        organizer_name = str(organizer_email_obj.get("name") or "").strip()
        organizer_email = str(organizer_email_obj.get("address") or "").strip()
        online_meeting_obj = row.get("onlineMeeting") if isinstance(row.get("onlineMeeting"), dict) else {}
        online_meeting_provider = str(row.get("onlineMeetingProvider") or "").strip().lower()
        online_meeting_url = str(
            online_meeting_obj.get("joinUrl")
            or row.get("onlineMeetingUrl")
            or ""
        ).strip()
        is_online_meeting = bool(row.get("isOnlineMeeting")) or bool(online_meeting_url)
        is_teams_meeting = "teams" in online_meeting_provider
        if not is_teams_meeting and online_meeting_url:
            is_teams_meeting = "teams.microsoft.com" in online_meeting_url.lower()

        events.append(
            {
                "event_id": event_id,
                "title": title,
                "due_date": due_date,
                "due_time": due_time,
                "is_completed": False,
                "status": status,
                "source_url": str(row.get("webLink") or "").strip(),
                "kind": "event",
                "provider": "outlook",
                "payload": {
                    "id": event_id,
                    "subject": title,
                    "is_all_day": is_all_day,
                    "is_cancelled": is_cancelled,
                    "response_status": response_value,
                    "organizer_name": organizer_name,
                    "organizer_email": organizer_email,
                    "is_online_meeting": is_online_meeting,
                    "online_meeting_provider": online_meeting_provider,
                    "online_meeting_url": online_meeting_url,
                    "teams_join_url": online_meeting_url if is_teams_meeting else "",
                    "is_teams_meeting": is_teams_meeting,
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "modified_at": str(row.get("lastModifiedDateTime") or "").strip(),
                    "web_link": str(row.get("webLink") or "").strip(),
                },
            }
        )
    return events


def _calendar_event_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    due_date = str(row.get("due_date") or "").strip()
    due_time = str(row.get("due_time") or "").strip()
    title = str(row.get("title") or "").strip().lower()
    return (due_date or "9999-12-31", due_time or "99:99", title)


def _calendar_event_id_set(rows: list[dict[str, Any]]) -> set[str]:
    event_ids: set[str] = set()
    for row in rows:
        event_id = str(row.get("event_id") or "").strip()
        if event_id:
            event_ids.add(event_id)
    return event_ids


def _new_calendar_events(*, previous_ids: set[str], current_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for row in current_rows:
        event_id = str(row.get("event_id") or "").strip()
        if not event_id or event_id in previous_ids:
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in {"cancelled", "declined", "completed"}:
            continue
        if bool(row.get("is_completed")):
            continue
        created.append(row)
    created.sort(key=_calendar_event_sort_key)
    return created


def _dispatch_calendar_sync_alert(*, user, provider: str, new_events: list[dict[str, Any]]) -> None:
    if user is None:
        return
    if not isinstance(new_events, list) or not new_events:
        return

    settings_payload = get_user_calendar_notification_settings(user)
    candidate_channels: list[str] = []
    if bool(settings_payload.get("calendar_events_app_enabled", True)):
        candidate_channels.append("app")
    if bool(settings_payload.get("calendar_events_sms_enabled", False)):
        candidate_channels.append("sms")
    if bool(settings_payload.get("calendar_events_email_enabled", False)):
        candidate_channels.append("email")
    if not candidate_channels:
        return

    resolved_provider = str(provider or "calendar").strip().lower() or "calendar"
    preview_rows = new_events[:_CALENDAR_ALERT_PREVIEW_LIMIT]
    preview_lines: list[str] = []
    for row in preview_rows:
        title = str(row.get("title") or "").strip() or "Untitled event"
        due_date = str(row.get("due_date") or "").strip()
        due_time = str(row.get("due_time") or "").strip()
        when = due_date or "unscheduled"
        if due_time:
            when = f"{when} {due_time}"
        preview_lines.append(f"- {title} ({when})")

    count = len(new_events)
    plural = "s" if count != 1 else ""
    subject = f"[Alshival] {count} new {resolved_provider} calendar event{plural}"
    body_lines = [
        f"Provider: {resolved_provider}",
        f"New events: {count}",
        "",
        *preview_lines,
    ]
    if count > len(preview_rows):
        body_lines.append(f"...and {count - len(preview_rows)} more.")
    body = "\n".join(body_lines).strip()
    first_title = str(preview_rows[0].get("title") or "").strip() if preview_rows else ""
    sms_body = f"{count} new {resolved_provider} calendar event{plural}"
    if first_title:
        sms_body = f"{sms_body}: {first_title[:90]}"

    allowed_channels = _alert_filter_allowed_channels(
        recipient=user,
        alert_kind="calendar_event",
        candidate_channels=candidate_channels,
        subject=subject,
        body=body,
        context={
            "provider": resolved_provider,
            "new_event_count": count,
            "preview_events": [
                {
                    "event_id": str(row.get("event_id") or "").strip(),
                    "title": str(row.get("title") or "").strip(),
                    "due_date": str(row.get("due_date") or "").strip(),
                    "due_time": str(row.get("due_time") or "").strip(),
                    "status": str(row.get("status") or "").strip(),
                }
                for row in preview_rows
            ],
        },
    )
    if not allowed_channels:
        return

    metadata = {
        "source": "calendar_sync",
        "provider": resolved_provider,
        "new_event_count": count,
    }
    if "app" in allowed_channels:
        notification_id = add_user_notification(
            user,
            kind="calendar_event_alert",
            title=subject,
            body=body,
            level="info",
            channel="app",
            metadata=metadata,
        )
        if notification_id:
            _log_calendar_alert_context_event_safe(
                user=user,
                event_type="alert_calendar_app",
                summary=f"{count} new {resolved_provider} calendar event{'s' if count != 1 else ''} delivered in app.",
                payload={
                    "provider": resolved_provider,
                    "channel": "app",
                    "new_event_count": count,
                },
            )
    if "sms" in allowed_channels:
        sms_sent, sms_error = _send_transition_sms(recipient=user, message=sms_body)
        if sms_sent:
            _log_calendar_alert_context_event_safe(
                user=user,
                event_type="alert_calendar_sms",
                summary=f"{count} new {resolved_provider} calendar event{'s' if count != 1 else ''} sent by SMS.",
                payload={
                    "provider": resolved_provider,
                    "channel": "sms",
                    "new_event_count": count,
                },
            )
        elif sms_error:
            _log_calendar_alert_context_event_safe(
                user=user,
                event_type="alert_calendar_sms_failed",
                summary=f"{resolved_provider} calendar SMS alert failed.",
                payload={
                    "provider": resolved_provider,
                    "channel": "sms",
                    "new_event_count": count,
                    "error": sms_error,
                },
            )
    if "email" in allowed_channels:
        email_sent, email_error = _send_transition_email(recipient=user, subject=subject, message=body)
        if email_sent:
            _log_calendar_alert_context_event_safe(
                user=user,
                event_type="alert_calendar_email",
                summary=f"{count} new {resolved_provider} calendar event{'s' if count != 1 else ''} sent by email.",
                payload={
                    "provider": resolved_provider,
                    "channel": "email",
                    "new_event_count": count,
                },
            )
        elif email_error:
            _log_calendar_alert_context_event_safe(
                user=user,
                event_type="alert_calendar_email_failed",
                summary=f"{resolved_provider} calendar email alert failed.",
                payload={
                    "provider": resolved_provider,
                    "channel": "email",
                    "new_event_count": count,
                    "error": email_error,
                },
            )


def refresh_calendar_cache_for_user(
    user,
    *,
    provider: str = "all",
    force: bool = False,
    min_interval_seconds: int = DEFAULT_CALENDAR_REFRESH_MIN_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """
    Refresh provider-backed calendar cache for a user.

    - Uses per-provider sync state in member.db to throttle refresh calls.
    - Returns structured per-provider status payload.
    - Currently Asana-backed; provider surface is designed for Outlook/Gmail expansion.
    """
    resolved_provider = str(provider or "").strip().lower() or "all"
    throttle_window = max(0, int(min_interval_seconds or 0))
    now_epoch = int(time.time())
    result: dict[str, Any] = {}

    if resolved_provider in {"all", "asana"}:
        state = get_user_calendar_sync_state(user, provider="asana") or {}
        previous_asana_rows = list_user_calendar_event_cache(
            user,
            provider="asana",
            limit=5000,
        )
        previous_asana_ids = _calendar_event_id_set(previous_asana_rows)
        should_force_refresh, age_seconds, last_epoch = _refresh_decision(
            state=state,
            now_epoch=now_epoch,
            force=bool(force),
            min_interval_seconds=throttle_window,
        )

        # Deferred import avoids circular import at module import time.
        from .views import (
            _ASANA_FULL_IMPORT_CACHE_KEY,
            _ASANA_FULL_IMPORT_TASK_FETCH_LIMIT,
            _asana_overview_context_for_user,
        )

        asana_context = _asana_overview_context_for_user(
            user,
            force_refresh=bool(should_force_refresh),
            cache_key=_ASANA_FULL_IMPORT_CACHE_KEY,
            task_fetch_limit=_ASANA_FULL_IMPORT_TASK_FETCH_LIMIT,
            run_auto_assign=bool(should_force_refresh),
            write_calendar_cache=True,
        )
        asana_rows = list_user_calendar_event_cache(
            user,
            provider="asana",
            limit=2000,
        )
        if bool(should_force_refresh):
            asana_error = str(asana_context.get("error") or "").strip()
            if not asana_error:
                new_asana_events = _new_calendar_events(
                    previous_ids=previous_asana_ids,
                    current_rows=asana_rows,
                )
                _dispatch_calendar_sync_alert(
                    user=user,
                    provider="asana",
                    new_events=new_asana_events,
                )

        result["asana"] = {
            "connected": bool(asana_context.get("connected")),
            "error": str(asana_context.get("error") or "").strip(),
            "task_count": int(asana_context.get("task_count") or 0),
            "cached_events": len(asana_rows),
            "synced_display": str(asana_context.get("synced_display") or "").strip(),
            "refresh_attempted": bool(should_force_refresh),
            "refresh_skipped": not bool(should_force_refresh),
            "refresh_skip_reason": "throttled" if not bool(should_force_refresh) else "",
            "refresh_age_seconds": int(age_seconds) if age_seconds is not None else None,
            "min_interval_seconds": int(throttle_window),
            "previous_synced_display": _display_from_epoch(last_epoch),
        }

    if resolved_provider in {"all", "outlook"}:
        state = get_user_calendar_sync_state(user, provider="outlook") or {}
        previous_outlook_rows = list_user_calendar_event_cache(
            user,
            provider="outlook",
            limit=5000,
        )
        previous_outlook_ids = _calendar_event_id_set(previous_outlook_rows)
        should_force_refresh, age_seconds, last_epoch = _refresh_decision(
            state=state,
            now_epoch=now_epoch,
            force=bool(force),
            min_interval_seconds=throttle_window,
        )
        connected, access_token, connection_error = _microsoft_access_token_for_user(user)
        fetch_error = ""
        event_count = 0
        truncated = False
        synced_display = _display_from_epoch(last_epoch)

        if should_force_refresh and connected and access_token:
            graph_events, truncated, fetch_error = _fetch_outlook_events(access_token)
            if not fetch_error:
                normalized_events = _outlook_calendar_cache_events(graph_events)
                event_count = len(normalized_events)
                replace_user_calendar_event_cache(
                    user,
                    provider="outlook",
                    events=normalized_events,
                    fetched_at_epoch=now_epoch,
                    status="ok",
                    message="truncated" if truncated else "",
                )
                synced_display = _display_from_epoch(now_epoch)
            else:
                set_user_calendar_sync_state(
                    user,
                    provider="outlook",
                    fetched_at_epoch=now_epoch,
                    item_count=_state_item_count(state if isinstance(state, dict) else {}),
                    status="error",
                    message=fetch_error,
                )
                synced_display = _display_from_epoch(now_epoch)
        elif should_force_refresh and connected and not access_token and connection_error:
            set_user_calendar_sync_state(
                user,
                provider="outlook",
                fetched_at_epoch=now_epoch,
                item_count=_state_item_count(state if isinstance(state, dict) else {}),
                status="error",
                message=connection_error,
            )
            synced_display = _display_from_epoch(now_epoch)

        outlook_rows = list_user_calendar_event_cache(
            user,
            provider="outlook",
            limit=2000,
        )
        if event_count <= 0:
            event_count = len(outlook_rows)
        if bool(should_force_refresh) and connected and access_token and not fetch_error:
            new_outlook_events = _new_calendar_events(
                previous_ids=previous_outlook_ids,
                current_rows=outlook_rows,
            )
            _dispatch_calendar_sync_alert(
                user=user,
                provider="outlook",
                new_events=new_outlook_events,
            )

        resolved_error = ""
        if connection_error and (should_force_refresh or not connected):
            resolved_error = connection_error
        if fetch_error:
            resolved_error = fetch_error

        result["outlook"] = {
            "connected": bool(connected),
            "error": str(resolved_error or "").strip(),
            "event_count": int(event_count),
            "cached_events": len(outlook_rows),
            "truncated": bool(truncated),
            "synced_display": str(synced_display or "").strip(),
            "refresh_attempted": bool(should_force_refresh),
            "refresh_skipped": not bool(should_force_refresh),
            "refresh_skip_reason": "throttled" if not bool(should_force_refresh) else "",
            "refresh_age_seconds": int(age_seconds) if age_seconds is not None else None,
            "min_interval_seconds": int(throttle_window),
            "previous_synced_display": _display_from_epoch(last_epoch),
        }

    return result
