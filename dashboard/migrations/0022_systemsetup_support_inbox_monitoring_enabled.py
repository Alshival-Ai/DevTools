from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0021_supportinboxmessage_and_sync_cursor"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsetup",
            name="support_inbox_monitoring_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
