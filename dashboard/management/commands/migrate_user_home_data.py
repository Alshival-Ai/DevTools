from __future__ import annotations

import filecmp
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from dashboard.resources_store import _migrate_legacy_user_root_files


_USER_DIR_RE = re.compile(r".+-\d+$")


def _owner_dir_for_user(user) -> Path:
    username = user.get_username() or f"user-{user.pk}"
    safe_username = slugify(username) or f"user-{user.pk}"
    return Path(getattr(settings, "USER_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "user_data")) / (
        f"{safe_username}-{int(user.pk)}"
    )


def _is_user_owner_dir(path: Path) -> bool:
    return bool(_USER_DIR_RE.fullmatch(str(path.name or "").strip()))


def _prune_empty_tree(path: Path) -> int:
    if not path.exists() or not path.is_dir():
        return 0
    removed = 0
    for child in sorted(path.iterdir(), key=lambda item: str(item), reverse=True):
        if child.is_dir():
            removed += _prune_empty_tree(child)
    try:
        next(path.iterdir())
    except StopIteration:
        try:
            path.rmdir()
            return removed + 1
        except Exception:
            return removed
    return removed


def _legacy_path_redundant(*, legacy_path: Path, target_path: Path) -> bool:
    if not legacy_path.exists() or not target_path.exists():
        return False
    if legacy_path.is_file():
        if not target_path.is_file():
            return False
        try:
            return filecmp.cmp(str(legacy_path), str(target_path), shallow=False)
        except Exception:
            return False
    if legacy_path.is_dir():
        if not target_path.is_dir():
            return False
        try:
            children = sorted(legacy_path.iterdir(), key=lambda item: str(item))
        except Exception:
            return False
        for child in children:
            if not _legacy_path_redundant(
                legacy_path=child,
                target_path=target_path / child.name,
            ):
                return False
        return True
    return False


def _remove_path(path: Path) -> tuple[int, int]:
    try:
        if path.is_dir():
            shutil.rmtree(path)
            return 0, 1
        path.unlink(missing_ok=True)
        return 1, 0
    except Exception:
        return 0, 0


def _finalize_legacy_path(*, legacy_path: Path, target_path: Path, dry_run: bool) -> tuple[int, int, int]:
    if not legacy_path.exists():
        return 0, 0, 0
    if not target_path.exists():
        return 0, 0, 1

    if _legacy_path_redundant(legacy_path=legacy_path, target_path=target_path):
        if dry_run:
            return (0, 1, 0) if legacy_path.is_dir() else (1, 0, 0)
        removed_files, removed_dirs = _remove_path(legacy_path)
        if removed_files or removed_dirs:
            return removed_files, removed_dirs, 0
        return 0, 0, 1

    if not legacy_path.is_dir() or not target_path.is_dir():
        return 0, 0, 1

    removed_files = 0
    removed_dirs = 0
    conflicts = 0
    for child in sorted(legacy_path.iterdir(), key=lambda item: str(item)):
        child_files, child_dirs, child_conflicts = _finalize_legacy_path(
            legacy_path=child,
            target_path=target_path / child.name,
            dry_run=dry_run,
        )
        removed_files += child_files
        removed_dirs += child_dirs
        conflicts += child_conflicts

    try:
        next(legacy_path.iterdir())
    except StopIteration:
        if dry_run:
            removed_dirs += 1
        else:
            try:
                legacy_path.rmdir()
                removed_dirs += 1
            except Exception:
                pass

    return removed_files, removed_dirs, conflicts


def _finalize_legacy_user_root_files(*, owner_dir: Path, app_data_dir: Path, dry_run: bool) -> tuple[int, int, int]:
    removed_files = 0
    removed_dirs = 0
    conflicts = 0
    for name in ("member.db", "knowledge.db", "resources"):
        child_files, child_dirs, child_conflicts = _finalize_legacy_path(
            legacy_path=owner_dir / name,
            target_path=app_data_dir / name,
            dry_run=dry_run,
        )
        removed_files += child_files
        removed_dirs += child_dirs
        conflicts += child_conflicts
    return removed_files, removed_dirs, conflicts


class Command(BaseCommand):
    help = (
        "Migrate legacy per-user data from USER_DATA_ROOT/<user>/ into "
        "USER_DATA_ROOT/<user>/home/.alshival/ and prune empty legacy folders."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show migration actions without modifying files.",
        )
        parser.add_argument(
            "--username",
            action="append",
            default=[],
            help="Optional username filter (repeatable). If omitted, migrate all detected user folders.",
        )
        parser.add_argument(
            "--skip-prune",
            action="store_true",
            help="Skip empty-folder pruning under legacy USER_DATA_ROOT user folders.",
        )
        parser.add_argument(
            "--finalize",
            action="store_true",
            help=(
                "Attempt strict legacy cleanup by deleting only paths that are "
                "already fully mirrored under home/.alshival."
            ),
        )

    def _candidate_owner_dirs(self, usernames: list[str]) -> tuple[list[Path], int]:
        user_root = Path(getattr(settings, "USER_DATA_ROOT", Path(settings.BASE_DIR) / "var" / "user_data"))
        user_root.mkdir(parents=True, exist_ok=True)
        if not usernames:
            return (
                [
                    entry
                    for entry in sorted(user_root.iterdir(), key=lambda item: str(item))
                    if entry.is_dir() and _is_user_owner_dir(entry)
                ],
                0,
            )

        missing = 0
        owner_dirs: list[Path] = []
        seen: set[str] = set()
        User = get_user_model()
        for raw in usernames:
            username = str(raw or "").strip()
            if not username:
                continue
            user = User.objects.filter(username=username).first()
            if user is None:
                missing += 1
                continue
            owner_dir = _owner_dir_for_user(user)
            key = str(owner_dir)
            if key in seen:
                continue
            seen.add(key)
            owner_dirs.append(owner_dir)
        return owner_dirs, missing

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        skip_prune = bool(options.get("skip_prune"))
        finalize = bool(options.get("finalize"))
        usernames = [str(value or "").strip() for value in list(options.get("username") or []) if str(value or "").strip()]

        owner_dirs, missing_users = self._candidate_owner_dirs(usernames)
        migrated_member = 0
        migrated_knowledge = 0
        migrated_resources = 0
        pruned_dirs = 0
        finalized_files = 0
        finalized_dirs = 0
        finalize_conflicts = 0
        scanned = 0
        errors = 0

        for owner_dir in owner_dirs:
            scanned += 1
            try:
                owner_dir.mkdir(parents=True, exist_ok=True)
                app_data_dir = owner_dir / "home" / ".alshival"
                legacy_member = owner_dir / "member.db"
                legacy_knowledge = owner_dir / "knowledge.db"
                legacy_resources = owner_dir / "resources"
                target_member = app_data_dir / "member.db"
                target_knowledge = app_data_dir / "knowledge.db"
                target_resources = app_data_dir / "resources"
                target_member_preexisting = target_member.exists()
                target_knowledge_preexisting = target_knowledge.exists()
                target_resources_preexisting = target_resources.exists()

                had_legacy_member = legacy_member.exists()
                had_legacy_knowledge = legacy_knowledge.exists()
                had_legacy_resources = legacy_resources.exists() and legacy_resources.is_dir()

                if not dry_run:
                    _migrate_legacy_user_root_files(owner_dir=owner_dir, app_data_dir=app_data_dir)

                if had_legacy_member and (
                    (not target_member_preexisting and target_member.exists()) if not dry_run else not target_member_preexisting
                ):
                    migrated_member += 1
                if had_legacy_knowledge and (
                    (not target_knowledge_preexisting and target_knowledge.exists())
                    if not dry_run
                    else not target_knowledge_preexisting
                ):
                    migrated_knowledge += 1
                if had_legacy_resources and (
                    (not target_resources_preexisting and target_resources.exists())
                    if not dry_run
                    else not target_resources_preexisting
                ):
                    migrated_resources += 1

                if finalize:
                    path_files, path_dirs, path_conflicts = _finalize_legacy_user_root_files(
                        owner_dir=owner_dir,
                        app_data_dir=app_data_dir,
                        dry_run=dry_run,
                    )
                    finalized_files += path_files
                    finalized_dirs += path_dirs
                    finalize_conflicts += path_conflicts

                if dry_run or skip_prune:
                    continue

                pruned_dirs += _prune_empty_tree(owner_dir / "resources")
                for child in sorted(owner_dir.iterdir(), key=lambda item: str(item)):
                    if not child.is_dir():
                        continue
                    if child.name == "home":
                        continue
                    pruned_dirs += _prune_empty_tree(child)
            except Exception as exc:
                errors += 1
                self.stderr.write(
                    f"[migrate-user-home-data] skipped owner_dir={owner_dir} error={exc}"
                )
                continue

        self.stdout.write(
            "[migrate-user-home-data] complete "
            f"dry_run={dry_run} scanned={scanned} missing_users={missing_users} "
            f"migrated_member={migrated_member} migrated_knowledge={migrated_knowledge} "
            f"migrated_resources={migrated_resources} pruned_dirs={pruned_dirs} "
            f"finalize={finalize} finalized_files={finalized_files} finalized_dirs={finalized_dirs} "
            f"finalize_conflicts={finalize_conflicts} errors={errors} skip_prune={skip_prune}"
        )
