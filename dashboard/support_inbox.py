from __future__ import annotations

import html
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.db import transaction
from django.utils import timezone as django_timezone

from .email_branding import build_alshival_branded_email, build_alshival_branded_email_from_html
from .models import SupportInboxMessage
from .resources_store import _global_owner_dir
from .setup_state import get_or_create_setup_state, get_setup_state

_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _to_graph_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_graph_datetime(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _graph_app_credentials() -> tuple[str, str, str, str]:
    setup = get_or_create_setup_state()
    mailbox = str(getattr(setup, "microsoft_mailbox_email", "") or "").strip().lower() if setup else ""
    if not mailbox:
        mailbox = str(
            os.getenv("MICROSOFT_MAILBOX_EMAIL")
            or os.getenv("SUPPORT_EMAIL")
            or ""
        ).strip().lower()
    if not mailbox:
        raise RuntimeError("Microsoft Email Agent mailbox is not configured.")
    app = (
        SocialApp.objects.filter(provider="microsoft")
        .exclude(client_id__exact="")
        .exclude(secret__exact="")
        .order_by("id")
        .first()
    )
    if app is None:
        raise RuntimeError("Microsoft connector is not configured.")
    app_settings = dict(getattr(app, "settings", {}) or {})
    tenant_id = str(app_settings.get("tenant") or "").strip()
    client_id = str(getattr(app, "client_id", "") or "").strip()
    client_secret = str(getattr(app, "secret", "") or "").strip()
    if not (tenant_id and client_id and client_secret):
        raise RuntimeError("Microsoft connector credentials are incomplete.")
    return tenant_id, client_id, client_secret, mailbox


def _graph_token(*, tenant_id: str, client_id: str, client_secret: str) -> str:
    response = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": _GRAPH_SCOPE,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json() if response.content else {}
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Microsoft Graph token response missing access_token.")
    return token


def _iter_inbox_messages(*, token: str, mailbox: str, since_utc: datetime, max_pages: int = 20):
    filter_text = f"receivedDateTime ge { _to_graph_iso(since_utc) }"
    select_fields = ",".join(
        [
            "id",
            "internetMessageId",
            "conversationId",
            "subject",
            "receivedDateTime",
            "bodyPreview",
            "hasAttachments",
            "body",
            "from",
            "webLink",
        ]
    )
    url = (
        f"{_GRAPH_BASE}/users/{quote(mailbox)}/mailFolders/Inbox/messages"
        f"?$top=100&$orderby=receivedDateTime asc&$select={select_fields}&$filter={quote(filter_text)}"
    )
    headers = {"Authorization": f"Bearer {token}"}
    pages = 0
    while url and pages < max_pages:
        pages += 1
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json() if response.content else {}
        for item in (payload.get("value") or []):
            if isinstance(item, dict):
                yield item
        url = str(payload.get("@odata.nextLink") or "").strip()


def _support_inbox_collection():
    import chromadb

    knowledge_path = _global_owner_dir() / "knowledge.db"
    client = chromadb.PersistentClient(path=str(knowledge_path))
    return client.get_or_create_collection(name="support_inbox")


def _stable_json_hash(value: object) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except Exception:
        payload = str(value or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _collection_metadata_hash_map(collection, *, ids: list[str], key: str) -> dict[str, str]:
    record_ids = [str(item or "").strip() for item in ids if str(item or "").strip()]
    if collection is None or not record_ids or not key:
        return {}
    try:
        payload = collection.get(ids=record_ids, include=["metadatas"])
    except Exception:
        return {}
    payload_ids = payload.get("ids") if isinstance(payload, dict) else []
    payload_metas = payload.get("metadatas") if isinstance(payload, dict) else []
    if isinstance(payload_ids, list) and payload_ids and isinstance(payload_ids[0], list):
        payload_ids = payload_ids[0]
    if isinstance(payload_metas, list) and payload_metas and isinstance(payload_metas[0], list):
        payload_metas = payload_metas[0]
    if not isinstance(payload_ids, list) or not isinstance(payload_metas, list):
        return {}
    hash_by_id: dict[str, str] = {}
    for idx, item_id in enumerate(payload_ids):
        resolved_id = str(item_id or "").strip()
        if not resolved_id:
            continue
        metadata = payload_metas[idx] if idx < len(payload_metas) and isinstance(payload_metas[idx], dict) else {}
        hash_by_id[resolved_id] = str(metadata.get(key) or "").strip()
    return hash_by_id


def _message_document(item: SupportInboxMessage) -> str:
    return "\n".join(
        [
            f"Mailbox: {item.mailbox}",
            f"From: {item.sender_email or 'unknown'}",
            f"Subject: {item.subject or '(no subject)'}",
            f"Received: {item.received_at.astimezone(timezone.utc).isoformat()}",
            "",
            item.body_text or item.body_preview or "",
        ]
    ).strip()


def _message_context_hash(item: SupportInboxMessage) -> str:
    payload = {
        "mailbox": item.mailbox,
        "message_id": item.message_id,
        "internet_message_id": item.internet_message_id or "",
        "conversation_id": item.conversation_id or "",
        "sender_email": item.sender_email or "",
        "subject": item.subject or "",
        "received_at": item.received_at.astimezone(timezone.utc).isoformat(),
        "has_attachments": bool(item.has_attachments),
        "body_preview": item.body_preview or "",
        "body_text": item.body_text or "",
    }
    return _stable_json_hash(payload)


def _upsert_support_inbox_knowledge(rows: list[SupportInboxMessage]) -> int:
    if not rows:
        return 0
    collection = _support_inbox_collection()
    ids: list[str] = []
    docs: list[str] = []
    metas: list[dict[str, str | int | float | bool]] = []
    row_hashes: list[str] = []
    for row in rows:
        ids.append(f"{row.mailbox}:{row.message_id}")
        docs.append(_message_document(row))
        context_hash = _message_context_hash(row)
        row_hashes.append(context_hash)
        metas.append(
            {
                "source": "support_inbox",
                "entry_type": "support_inbox_email",
                "mailbox": row.mailbox,
                "message_id": row.message_id,
                "internet_message_id": row.internet_message_id or "",
                "conversation_id": row.conversation_id or "",
                "sender_email": row.sender_email or "",
                "subject": row.subject or "",
                "received_at": row.received_at.astimezone(timezone.utc).isoformat(),
                "has_attachments": bool(row.has_attachments),
                "context_hash": context_hash,
            }
        )
    existing_hashes = _collection_metadata_hash_map(
        collection,
        ids=ids,
        key="context_hash",
    )
    filtered_ids: list[str] = []
    filtered_docs: list[str] = []
    filtered_metas: list[dict[str, str | int | float | bool]] = []
    for idx, item_id in enumerate(ids):
        current_hash = row_hashes[idx] if idx < len(row_hashes) else ""
        if existing_hashes.get(item_id, "") == current_hash:
            continue
        filtered_ids.append(item_id)
        filtered_docs.append(docs[idx] if idx < len(docs) else "")
        filtered_metas.append(metas[idx] if idx < len(metas) else {})
    if not filtered_ids:
        return 0
    collection.upsert(ids=filtered_ids, documents=filtered_docs, metadatas=filtered_metas)
    return len(filtered_ids)


def _header_safe(value: str, *, limit: int = 180) -> str:
    cleaned = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()
    return cleaned


def send_support_inbox_email(
    *,
    recipient_email: str,
    subject: str,
    body_text: str,
    body_html: str = "",
    initiated_by_user_id: int | None = None,
    initiated_by_username: str = "",
    initiated_by_email: str = "",
    initiated_by_channel: str = "",
    initiated_by_conversation_id: str = "",
) -> tuple[bool, str]:
    setup = get_setup_state()
    if setup is None:
        return False, "setup_missing"
    if not bool(getattr(setup, "support_inbox_monitoring_enabled", False)):
        return False, "support_inbox_monitoring_disabled"

    to_address = str(recipient_email or "").strip().lower()
    if not to_address:
        return False, "missing_email_address"

    try:
        tenant_id, client_id, client_secret, mailbox = _graph_app_credentials()
        token = _graph_token(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    except Exception as exc:
        return False, f"support_inbox_config_error:{exc}"

    resolved_html_input = str(body_html or "").strip()
    if resolved_html_input:
        resolved_subject, _resolved_text_body, resolved_html_body = build_alshival_branded_email_from_html(
            subject,
            body_text,
            resolved_html_input,
        )
    else:
        resolved_subject, _resolved_text_body, resolved_html_body = build_alshival_branded_email(subject, body_text)
    payload = {
        "message": {
            "subject": str(resolved_subject or "").strip()[:255] or "Alshival notification",
            "body": {
                "contentType": "HTML",
                "content": resolved_html_body,
            },
            "toRecipients": [{"emailAddress": {"address": to_address}}],
            "internetMessageHeaders": [
                {
                    "name": "X-Alshival-Initiated-By-UserId",
                    "value": _header_safe(str(int(initiated_by_user_id or 0))),
                },
                {
                    "name": "X-Alshival-Initiated-By-Username",
                    "value": _header_safe(initiated_by_username),
                },
                {
                    "name": "X-Alshival-Initiated-By-Email",
                    "value": _header_safe(initiated_by_email),
                },
                {
                    "name": "X-Alshival-Initiated-By-Channel",
                    "value": _header_safe(initiated_by_channel),
                },
                {
                    "name": "X-Alshival-Initiated-By-ConversationId",
                    "value": _header_safe(initiated_by_conversation_id),
                },
            ],
        },
        "saveToSentItems": True,
    }
    try:
        response = requests.post(
            f"{_GRAPH_BASE}/users/{quote(mailbox)}/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        return False, f"support_inbox_send_failed:{exc}"

    if 200 <= int(response.status_code) < 300:
        return True, ""
    detail = ""
    try:
        body = response.json() if response.content else {}
        if isinstance(body, dict):
            error_obj = body.get("error") if isinstance(body.get("error"), dict) else {}
            detail = str(error_obj.get("message") or "").strip()
    except Exception:
        detail = ""
    if detail:
        return False, f"support_inbox_status_{int(response.status_code)}:{detail}"
    return False, f"support_inbox_status_{int(response.status_code)}"


def poll_support_inbox_once(*, initial_lookback_minutes: int = 60, max_pages: int = 20) -> dict[str, int | str]:
    setup = get_or_create_setup_state()
    tenant_id, client_id, client_secret, mailbox = _graph_app_credentials()
    token = _graph_token(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)

    now_utc = django_timezone.now().astimezone(timezone.utc)
    since = getattr(setup, "support_inbox_last_synced_at", None) if setup else None
    if since is None:
        since = now_utc - timedelta(minutes=max(5, int(initial_lookback_minutes)))
    else:
        since = since.astimezone(timezone.utc) - timedelta(seconds=2)

    created_or_updated = 0
    knowledge_upserted = 0
    newest_seen: datetime | None = None
    changed_rows: list[SupportInboxMessage] = []

    for item in _iter_inbox_messages(token=token, mailbox=mailbox, since_utc=since, max_pages=max_pages):
        message_id = str(item.get("id") or "").strip()
        if not message_id:
            continue
        received_at = _parse_graph_datetime(str(item.get("receivedDateTime") or ""))
        if received_at is None:
            continue
        sender = item.get("from") if isinstance(item.get("from"), dict) else {}
        email_obj = sender.get("emailAddress") if isinstance(sender.get("emailAddress"), dict) else {}
        sender_email = str(email_obj.get("address") or "").strip().lower()
        sender_name = str(email_obj.get("name") or "").strip()
        body_obj = item.get("body") if isinstance(item.get("body"), dict) else {}
        body_type = str(body_obj.get("contentType") or "").strip().lower()
        body_content = str(body_obj.get("content") or "").strip()
        body_text = _strip_html(body_content) if body_type == "html" else re.sub(r"\s+", " ", body_content).strip()
        body_preview = re.sub(r"\s+", " ", str(item.get("bodyPreview") or "")).strip()

        payload = {
            "mailbox": mailbox,
            "message_id": message_id,
            "internet_message_id": str(item.get("internetMessageId") or "").strip(),
            "conversation_id": str(item.get("conversationId") or "").strip(),
            "sender_email": sender_email,
            "sender_name": sender_name,
            "subject": str(item.get("subject") or "").strip(),
            "received_at": received_at,
            "body_preview": body_preview,
            "body_text": body_text or body_preview,
            "has_attachments": bool(item.get("hasAttachments")),
            "web_link": str(item.get("webLink") or "").strip(),
            "raw_payload": item,
        }

        with transaction.atomic():
            obj, _created = SupportInboxMessage.objects.update_or_create(
                mailbox=mailbox,
                message_id=message_id,
                defaults=payload,
            )
        changed_rows.append(obj)
        created_or_updated += 1
        if newest_seen is None or received_at > newest_seen:
            newest_seen = received_at

    if changed_rows:
        knowledge_upserted = _upsert_support_inbox_knowledge(changed_rows)

    if setup and newest_seen is not None:
        setup.support_inbox_last_synced_at = newest_seen
        setup.save(update_fields=["support_inbox_last_synced_at", "updated_at"])

    return {
        "mailbox": mailbox,
        "ingested_messages": int(created_or_updated),
        "knowledge_upserted": int(knowledge_upserted),
        "since": _to_graph_iso(since),
    }


def _resolve_verified_user_for_sender_email(sender_email: str):
    resolved_email = str(sender_email or "").strip().lower()
    if not resolved_email:
        return None, "missing_sender_email"
    try:
        from allauth.account.models import EmailAddress
    except Exception:
        return None, "email_verification_model_unavailable"

    try:
        match = (
            EmailAddress.objects.select_related("user")
            .filter(
                email__iexact=resolved_email,
                verified=True,
                user__is_active=True,
            )
            .order_by("-primary", "id")
            .first()
        )
    except Exception as exc:
        return None, f"user_lookup_failed:{exc}"
    if match is None:
        email_verification_mode = str(getattr(settings, "ACCOUNT_EMAIL_VERIFICATION", "") or "").strip().lower()
        if email_verification_mode == "none":
            try:
                from django.contrib.auth import get_user_model

                User = get_user_model()
                user = (
                    User.objects.filter(
                        email__iexact=resolved_email,
                        is_active=True,
                    )
                    .order_by("id")
                    .first()
                )
            except Exception as exc:
                return None, f"user_lookup_failed:{exc}"
            if user is not None:
                return user, ""
        return None, "sender_email_not_verified"
    return getattr(match, "user", None), ""


def _support_inbox_reply_subject(subject: str) -> str:
    resolved_subject = str(subject or "").strip()
    if not resolved_subject:
        return "Re: Your message to Alshival"
    if resolved_subject.lower().startswith("re:"):
        return resolved_subject[:255]
    return f"Re: {resolved_subject}"[:255]


def _support_inbox_agent_email_prompt(message: SupportInboxMessage) -> str:
    lines = [
        "You received an email through the shared support inbox.",
        f"From: {str(message.sender_email or '').strip().lower()}",
        f"Subject: {str(message.subject or '').strip()}",
        f"Received (UTC): {message.received_at.astimezone(timezone.utc).isoformat()}",
        "",
        "Email body:",
        str(message.body_text or message.body_preview or "").strip()[:12000],
    ]
    return "\n".join(lines).strip()


def _looks_like_html_fragment(value: str) -> bool:
    content = str(value or "").strip()
    if not content:
        return False
    return bool(re.search(r"<[a-zA-Z][^>]*>", content))


def _process_support_inbox_email_with_agent(message: SupportInboxMessage, *, mailbox: str) -> tuple[str, str]:
    sender_email = str(message.sender_email or "").strip().lower()
    if not sender_email:
        return "skipped_missing_sender", "sender email is missing"
    if sender_email == str(mailbox or "").strip().lower():
        return "skipped_mailbox_sender", "sender email is the support mailbox"

    user, resolve_error = _resolve_verified_user_for_sender_email(sender_email)
    if user is None:
        return "skipped_unverified_sender", resolve_error or "sender is not a verified user"

    try:
        from .views import _ask_alshival_generate_reply_for_user
    except Exception as exc:
        return "error_agent_unavailable", f"ask agent import failed: {exc}"

    conversation_id = f"support-inbox:{str(message.message_id or '').strip()[:120]}"
    raw_message = _support_inbox_agent_email_prompt(message)
    extra_context_lines = [
        "- support_inbox_email_mode: true",
        f"- support_inbox_mailbox: {mailbox}",
        f"- support_inbox_message_id: {str(message.message_id or '').strip()}",
        f"- support_inbox_conversation_id: {str(message.conversation_id or '').strip()}",
        f"- support_inbox_sender_email: {sender_email}",
        "Email response policy:",
        "- Assume this sender is verified for this account.",
        "- Return an email-safe HTML fragment (no <html>, <head>, or <body> tag).",
        "- Use only inline CSS; avoid <style> and external assets.",
        "- Creative direction: tasteful decorative motifs like flowers, bumble bees, moon, and stars are encouraged.",
        "- Keep content useful first, with concise actionable guidance.",
        "- Do not include markdown code fences.",
        "- Do not ask the tool to send email; your final reply will be sent automatically.",
    ]
    try:
        reply_text, meta = _ask_alshival_generate_reply_for_user(
            user=user,
            raw_message=raw_message,
            conversation_id=conversation_id,
            channel="support_inbox_email",
            extra_context_lines=extra_context_lines,
        )
    except Exception as exc:
        return "error_agent_execution", f"ask agent execution failed: {exc}"
    if not reply_text:
        return "error_agent_empty", str((meta or {}).get("error") or "empty_reply")

    resolved_reply = str(reply_text or "").strip()
    reply_subject = _support_inbox_reply_subject(message.subject)
    reply_html_fragment = resolved_reply if _looks_like_html_fragment(resolved_reply) else ""
    reply_text_fallback = _strip_html(reply_html_fragment) if reply_html_fragment else resolved_reply
    sent, send_error = send_support_inbox_email(
        recipient_email=sender_email,
        subject=reply_subject,
        body_text=str(reply_text_fallback or "").strip()[:12000],
        body_html=reply_html_fragment[:24000],
        initiated_by_user_id=int(getattr(user, "id", 0) or 0),
        initiated_by_username=str(getattr(user, "username", "") or "").strip(),
        initiated_by_email=str(getattr(user, "email", "") or "").strip().lower(),
        initiated_by_channel="support_inbox_email",
        initiated_by_conversation_id=conversation_id,
    )
    if not sent:
        return "error_reply_send_failed", send_error or "support inbox send failed"

    message.agent_reply_subject = reply_subject[:500]
    message.agent_reply_preview = str(resolved_reply or "").strip()[:4000]
    message.agent_reply_sent_at = django_timezone.now()
    return "replied", ""


def run_support_inbox_email_agent_once(*, limit: int = 10) -> dict[str, int | str]:
    setup = get_setup_state()
    if setup is None:
        return {"status": "setup_missing", "processed": 0, "replied": 0, "skipped": 0, "errors": 0}
    if not bool(getattr(setup, "support_inbox_monitoring_enabled", False)):
        return {"status": "monitoring_disabled", "processed": 0, "replied": 0, "skipped": 0, "errors": 0}

    try:
        _tenant_id, _client_id, _client_secret, mailbox = _graph_app_credentials()
    except Exception as exc:
        return {
            "status": "config_error",
            "mailbox": "",
            "processed": 0,
            "replied": 0,
            "skipped": 0,
            "errors": 0,
            "error": str(exc),
        }

    resolved_limit = max(1, min(int(limit or 10), 100))
    rows = list(
        SupportInboxMessage.objects.filter(
            mailbox=str(mailbox or "").strip().lower(),
            agent_processed_at__isnull=True,
        )
        .order_by("received_at", "id")[:resolved_limit]
    )

    processed = 0
    replied = 0
    skipped = 0
    errors = 0
    for row in rows:
        processed += 1
        status, error = _process_support_inbox_email_with_agent(row, mailbox=mailbox)
        row.agent_status = str(status or "").strip()[:32]
        row.agent_last_error = str(error or "").strip()
        row.agent_processed_at = django_timezone.now()
        update_fields = ["agent_status", "agent_last_error", "agent_processed_at", "updated_at"]
        if row.agent_reply_sent_at is not None:
            update_fields.extend(["agent_reply_sent_at", "agent_reply_subject", "agent_reply_preview"])
        row.save(update_fields=update_fields)

        if status == "replied":
            replied += 1
        elif status.startswith("skipped_"):
            skipped += 1
        else:
            errors += 1

    return {
        "status": "ok",
        "mailbox": str(mailbox or "").strip().lower(),
        "processed": int(processed),
        "replied": int(replied),
        "skipped": int(skipped),
        "errors": int(errors),
    }
