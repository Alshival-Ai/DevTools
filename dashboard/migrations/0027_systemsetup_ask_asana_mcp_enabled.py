from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0026_alter_wikipage_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsetup",
            name="ask_asana_mcp_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
