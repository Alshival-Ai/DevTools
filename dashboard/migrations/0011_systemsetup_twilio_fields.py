from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0010_resourcepackageowner"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsetup",
            name="twilio_account_sid",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="systemsetup",
            name="twilio_auth_token",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="systemsetup",
            name="twilio_from_number",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
