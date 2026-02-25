from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0010_resourcepackageowner"),
    ]

    operations = [
        migrations.AddField(
            model_name="globalteamapikey",
            name="encrypted_key",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="globalteamapikey",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
