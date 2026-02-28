from __future__ import annotations

from .resources_store import list_resources, list_user_notifications
from .user_avatar import resolve_user_avatar_url


def sidebar_workspace_widget(request):
    payload = {
        "resources_total": 0,
        "resources_healthy": 0,
        "resources_unhealthy": 0,
        "resources_unknown": 0,
        "resources_attention": 0,
        "unread_notifications": 0,
        "team_count": 0,
    }
    user_menu = {
        "avatar_url": "",
        "initial": "U",
    }

    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return {
            "sidebar_workspace_widget": payload,
            "topbar_user_menu": user_menu,
        }

    display_name = (
        str(getattr(user, "get_full_name", lambda: "")() or "").strip()
        or str(getattr(user, "username", "") or "").strip()
        or "User"
    )
    user_menu["initial"] = display_name[:1].upper() if display_name else "U"
    try:
        user_menu["avatar_url"] = resolve_user_avatar_url(int(getattr(user, "id", 0) or 0))
    except Exception:
        user_menu["avatar_url"] = ""

    try:
        resources = list_resources(user)
    except Exception:
        resources = []

    unhealthy_count = 0
    unknown_count = 0
    for item in resources:
        status = str(getattr(item, "last_status", "") or "").strip().lower()
        if status in {"unhealthy", "failed", "error"}:
            unhealthy_count += 1
        elif status != "healthy":
            unknown_count += 1

    total_resources = len(resources)
    attention_count = unhealthy_count + unknown_count
    healthy_count = max(total_resources - attention_count, 0)

    try:
        notification_snapshot = list_user_notifications(user, limit=1)
        unread_count = int(notification_snapshot.get("unread_count") or 0)
    except Exception:
        unread_count = 0

    try:
        team_count = int(user.groups.count())
    except Exception:
        team_count = 0

    payload.update(
        {
            "resources_total": total_resources,
            "resources_healthy": healthy_count,
            "resources_unhealthy": unhealthy_count,
            "resources_unknown": unknown_count,
            "resources_attention": attention_count,
            "unread_notifications": unread_count,
            "team_count": team_count,
        }
    )
    return {
        "sidebar_workspace_widget": payload,
        "topbar_user_menu": user_menu,
    }
