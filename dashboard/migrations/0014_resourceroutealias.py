import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0013_usernotificationsettings"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ResourceRouteAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("resource_uuid", models.CharField(max_length=64)),
                (
                    "route_kind",
                    models.CharField(
                        choices=[("user", "User"), ("team", "Team")],
                        default="user",
                        max_length=16,
                    ),
                ),
                ("route_value", models.CharField(max_length=120)),
                ("is_current", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dashboard_resource_route_aliases_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "owner_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dashboard_resource_route_aliases",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dashboard_resource_route_aliases_updated",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-is_current", "-updated_at", "-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="resourceroutealias",
            constraint=models.UniqueConstraint(
                fields=("resource_uuid", "route_kind", "route_value"),
                name="dash_route_alias_uuid_kind_value_ux",
            ),
        ),
        migrations.AddIndex(
            model_name="resourceroutealias",
            index=models.Index(fields=["resource_uuid", "is_current"], name="dash_route_alias_uuid_curr_idx"),
        ),
        migrations.AddIndex(
            model_name="resourceroutealias",
            index=models.Index(fields=["route_kind", "route_value"], name="dash_route_alias_kind_val_idx"),
        ),
    ]
