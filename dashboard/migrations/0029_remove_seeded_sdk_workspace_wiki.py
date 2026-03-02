from django.db import migrations, models


LEGACY_PATHS = [
    "integrations/alshival-sdk-self-hosted-log-ingest",
]


def remove_seeded_sdk_workspace_wiki(apps, schema_editor):
    WikiPage = apps.get_model("dashboard", "WikiPage")
    workspace_scope = getattr(WikiPage, "SCOPE_WORKSPACE", "workspace")
    queryset = WikiPage.objects.filter(
        scope=workspace_scope,
        resource_uuid="",
    )
    path_filter = models.Q()
    for path in LEGACY_PATHS:
        path_filter |= models.Q(path__iexact=path)
    path_filter |= models.Q(path__icontains="alshival-sdk")
    queryset.filter(path_filter).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0028_supportinboxmessage_agent_fields"),
    ]

    operations = [
        migrations.RunPython(
            remove_seeded_sdk_workspace_wiki,
            migrations.RunPython.noop,
        ),
    ]
