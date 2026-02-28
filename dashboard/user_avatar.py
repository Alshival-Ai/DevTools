from __future__ import annotations

from collections.abc import Iterable

from allauth.socialaccount.models import SocialAccount

_AVATAR_EXTRA_DATA_KEYS = (
    "avatar_url",
    "picture",
    "avatar",
    "profile_image",
    "photo",
    "image",
)
_PROVIDER_PRIORITY = {
    "microsoft": 0,
    "github": 1,
}
_DEFAULT_PROVIDER_PRIORITY = 2


def _normalize_user_ids(user_ids: Iterable[int]) -> list[int]:
    normalized = {int(raw_id) for raw_id in user_ids if int(raw_id or 0) > 0}
    return sorted(normalized)


def _extract_avatar_url(extra_data: object) -> str:
    if not isinstance(extra_data, dict):
        return ""
    for key in _AVATAR_EXTRA_DATA_KEYS:
        avatar_url = str(extra_data.get(key) or "").strip()
        if avatar_url:
            return avatar_url
    return ""


def _provider_priority(provider: object) -> int:
    normalized_provider = str(provider or "").strip().lower()
    return _PROVIDER_PRIORITY.get(normalized_provider, _DEFAULT_PROVIDER_PRIORITY)


def resolve_user_avatar_urls(user_ids: Iterable[int]) -> dict[int, str]:
    normalized_user_ids = _normalize_user_ids(user_ids)
    if not normalized_user_ids:
        return {}

    best_by_user_id: dict[int, tuple[int, int, str]] = {}
    social_rows = (
        SocialAccount.objects.filter(user_id__in=normalized_user_ids)
        .order_by("user_id", "id")
        .values("id", "user_id", "provider", "extra_data")
    )
    for row in social_rows:
        user_id = int(row.get("user_id") or 0)
        if user_id <= 0:
            continue

        avatar_url = _extract_avatar_url(row.get("extra_data"))
        if not avatar_url:
            continue

        account_id = int(row.get("id") or 0)
        priority = _provider_priority(row.get("provider"))
        current = best_by_user_id.get(user_id)
        if current is None or (priority, account_id) < (current[0], current[1]):
            best_by_user_id[user_id] = (priority, account_id, avatar_url)

    return {user_id: payload[2] for user_id, payload in best_by_user_id.items()}


def resolve_user_avatar_url(user_id: int) -> str:
    normalized_user_id = int(user_id or 0)
    if normalized_user_id <= 0:
        return ""
    return resolve_user_avatar_urls([normalized_user_id]).get(normalized_user_id, "")
