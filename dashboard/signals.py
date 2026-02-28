from pathlib import Path
import shutil

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils.text import slugify

from .models import UserFeatureAccess, UserNotificationSettings
from .user_knowledge_store import delete_user_record, sync_all_user_records, upsert_user_record
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


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def sync_user_record_on_user_save(sender, instance, **kwargs):
    upsert_user_record(instance)


@receiver(post_delete, sender=settings.AUTH_USER_MODEL)
def sync_user_record_on_user_delete(sender, instance, **kwargs):
    delete_user_record(int(getattr(instance, "id", 0) or 0))


@receiver(post_save, sender=UserNotificationSettings)
def sync_user_record_on_notification_save(sender, instance: UserNotificationSettings, **kwargs):
    if instance.user_id:
        upsert_user_record(instance.user)


@receiver(post_delete, sender=UserNotificationSettings)
def sync_user_record_on_notification_delete(sender, instance: UserNotificationSettings, **kwargs):
    user_id = int(getattr(instance, "user_id", 0) or 0)
    if user_id <= 0:
        return
    User = get_user_model()
    user = User.objects.filter(id=user_id).first()
    if user is not None:
        upsert_user_record(user)
    else:
        delete_user_record(user_id)


@receiver(post_save, sender=UserFeatureAccess)
def sync_user_record_on_feature_access_save(sender, instance: UserFeatureAccess, **kwargs):
    if instance.user_id:
        upsert_user_record(instance.user)


@receiver(post_delete, sender=UserFeatureAccess)
def sync_user_record_on_feature_access_delete(sender, instance: UserFeatureAccess, **kwargs):
    user_id = int(getattr(instance, "user_id", 0) or 0)
    if user_id <= 0:
        return
    User = get_user_model()
    user = User.objects.filter(id=user_id).first()
    if user is not None:
        upsert_user_record(user)


@receiver(m2m_changed, sender=get_user_model().groups.through)
def sync_user_record_on_group_membership_change(sender, instance, action: str, reverse: bool, pk_set, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    User = get_user_model()
    if reverse:
        # reverse=True means instance is Group and pk_set contains user ids.
        user_ids = [int(item) for item in (pk_set or set()) if int(item or 0) > 0]
        if not user_ids and action == "post_clear":
            # post_clear does not provide removed user ids; resync all user records.
            sync_all_user_records()
            return
        for user in User.objects.filter(id__in=user_ids):
            upsert_user_record(user)
        return

    # reverse=False means instance is User.
    if int(getattr(instance, "id", 0) or 0) > 0:
        upsert_user_record(instance)
