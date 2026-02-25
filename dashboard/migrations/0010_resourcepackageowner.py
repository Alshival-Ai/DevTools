from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0009_wikipage_is_draft_wikipage_dash_wiki_draft_upd_idx"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ResourcePackageOwner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("resource_uuid", models.CharField(max_length=64, unique=True)),
                (
                    "owner_scope",
                    models.CharField(
                        choices=[("user", "User"), ("team", "Team"), ("global", "Global")],
                        default="user",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dashboard_resource_packages_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "owner_team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dashboard_resource_packages_owned",
                        to="auth.group",
                    ),
                ),
                (
                    "owner_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dashboard_resource_packages_owned",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dashboard_resource_packages_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="resourcepackageowner",
            index=models.Index(fields=["owner_scope", "updated_at"], name="dash_pkg_owner_scope_idx"),
        ),
        migrations.AddIndex(
            model_name="resourcepackageowner",
            index=models.Index(fields=["owner_user", "updated_at"], name="dash_pkg_owner_user_idx"),
        ),
        migrations.AddIndex(
            model_name="resourcepackageowner",
            index=models.Index(fields=["owner_team", "updated_at"], name="dash_pkg_owner_team_idx"),
        ),
    ]
