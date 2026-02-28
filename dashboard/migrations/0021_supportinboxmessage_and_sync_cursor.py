from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0020_systemsetup_microsoft_mailbox_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsetup",
            name="support_inbox_last_synced_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.CreateModel(
            name="SupportInboxMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mailbox", models.CharField(db_index=True, max_length=255)),
                ("message_id", models.CharField(max_length=255)),
                ("internet_message_id", models.CharField(blank=True, default="", max_length=512)),
                ("conversation_id", models.CharField(blank=True, default="", max_length=255)),
                ("sender_email", models.CharField(blank=True, default="", max_length=255)),
                ("sender_name", models.CharField(blank=True, default="", max_length=255)),
                ("subject", models.CharField(blank=True, default="", max_length=500)),
                ("received_at", models.DateTimeField(db_index=True)),
                ("body_preview", models.TextField(blank=True, default="")),
                ("body_text", models.TextField(blank=True, default="")),
                ("has_attachments", models.BooleanField(default=False)),
                ("web_link", models.TextField(blank=True, default="")),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-received_at", "-updated_at"],
            },
        ),
        migrations.AddIndex(
            model_name="supportinboxmessage",
            index=models.Index(fields=["mailbox", "received_at"], name="dash_sup_inbox_mail_recv_idx"),
        ),
        migrations.AddConstraint(
            model_name="supportinboxmessage",
            constraint=models.UniqueConstraint(fields=("mailbox", "message_id"), name="dash_sup_inbox_mail_msg_ux"),
        ),
    ]
