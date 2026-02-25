from pathlib import Path
import shutil

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.text import slugify

from .web_terminal import ensure_local_shell_home_for_user


def _user_folder_base() -> Path:
    base = getattr(settings, 'USER_DATA_ROOT', None)
    if base:
        return Path(base)
    return Path(settings.BASE_DIR) / 'user_data'


def _team_folder_base() -> Path:
    base = getattr(settings, 'TEAM_DATA_ROOT', None)
    if base:
        return Path(base)
    return Path(settings.BASE_DIR) / 'var' / 'team_data'


@receiver(user_logged_in)
def ensure_user_folder(sender, request, user, **kwargs):
    base_dir = _user_folder_base()
    base_dir.mkdir(parents=True, exist_ok=True)

    username = user.get_username() or f"user-{user.pk}"
    safe_username = slugify(username) or f"user-{user.pk}"
    user_dir = base_dir / f"{safe_username}-{user.pk}"
    user_dir.mkdir(parents=True, exist_ok=True)


def ensure_team_folder(group: Group) -> Path:
    base_dir = _team_folder_base()
    base_dir.mkdir(parents=True, exist_ok=True)

    team_name = group.name or f"team-{group.pk}"
    safe_team_name = slugify(team_name) or f"team-{group.pk}"
    team_dir = base_dir / f"{safe_team_name}-{group.pk}"
    team_dir.mkdir(parents=True, exist_ok=True)
    return team_dir


def cleanup_team_folder(group: Group) -> None:
    base_dir = _team_folder_base()
    if not base_dir.exists():
        return

    team_name = group.name or f"team-{group.pk}"
    safe_team_name = slugify(team_name) or f"team-{group.pk}"
    team_id_suffix = f"-{group.pk}"

    candidates = {
        (base_dir / f"{safe_team_name}-{group.pk}").resolve(),
        (base_dir / f"team-{group.pk}").resolve(),
    }
    for entry in base_dir.glob(f"*{team_id_suffix}"):
        candidates.add(entry.resolve())

    base_dir_resolved = base_dir.resolve()
    for candidate in candidates:
        # Guard against accidental deletion outside TEAM_DATA_ROOT.
        if not candidate.is_dir() or not candidate.is_relative_to(base_dir_resolved):
            continue
        shutil.rmtree(candidate, ignore_errors=True)


@receiver(post_save, sender=Group)
def ensure_group_folder_on_create(sender, instance: Group, created: bool, **kwargs):
    if not created:
        return
    ensure_team_folder(instance)


@receiver(post_delete, sender=Group)
def cleanup_group_folder_on_delete(sender, instance: Group, **kwargs):
    cleanup_team_folder(instance)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_superuser_shell_home(sender, instance, **kwargs):
    if not bool(getattr(instance, "is_superuser", False)):
        return
    ensure_local_shell_home_for_user(instance)
