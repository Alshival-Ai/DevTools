from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dashboard", "0022_systemsetup_support_inbox_monitoring_enabled"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserInvite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(max_length=96, unique=True)),
                ("invited_username", models.CharField(blank=True, default="", max_length=150)),
                ("invited_email", models.CharField(blank=True, default="", max_length=255)),
                ("invited_phone", models.CharField(blank=True, default="", max_length=32)),
                (
                    "delivery_channel",
                    models.CharField(
                        choices=[("email", "Email"), ("sms", "SMS")],
                        default="email",
                        max_length=16,
                    ),
                ),
                ("sent_to", models.CharField(blank=True, default="", max_length=255)),
                ("allowed_signup_methods", models.JSONField(blank=True, default=list)),
                ("team_names", models.JSONField(blank=True, default=list)),
                ("feature_keys", models.JSONField(blank=True, default=list)),
                ("is_staff", models.BooleanField(default=False)),
                ("is_superuser", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("expires_at", models.DateTimeField()),
                ("accepted_at", models.DateTimeField(blank=True, default=None, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "accepted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="dashboard_user_invites_accepted",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="dashboard_user_invites_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="userinvite",
            index=models.Index(fields=["token"], name="dash_user_invite_token_idx"),
        ),
        migrations.AddIndex(
            model_name="userinvite",
            index=models.Index(fields=["invited_email", "expires_at"], name="dash_user_invite_email_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="userinvite",
            index=models.Index(fields=["invited_phone", "expires_at"], name="dash_user_invite_phone_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="userinvite",
            index=models.Index(fields=["accepted_at", "expires_at"], name="dash_user_invite_acc_exp_idx"),
        ),
    ]
