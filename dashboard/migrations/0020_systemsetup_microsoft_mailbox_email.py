from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0019_wikipage_scope_and_resource"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsetup",
            name="microsoft_mailbox_email",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
