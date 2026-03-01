from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0027_systemsetup_ask_asana_mcp_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportinboxmessage",
            name="agent_last_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="supportinboxmessage",
            name="agent_processed_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="supportinboxmessage",
            name="agent_reply_preview",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="supportinboxmessage",
            name="agent_reply_sent_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="supportinboxmessage",
            name="agent_reply_subject",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="supportinboxmessage",
            name="agent_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddIndex(
            model_name="supportinboxmessage",
            index=models.Index(fields=["mailbox", "agent_processed_at"], name="dash_sup_inbox_agent_proc_idx"),
        ),
    ]
