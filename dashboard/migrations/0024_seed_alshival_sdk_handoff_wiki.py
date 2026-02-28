from django.db import migrations


DEFAULT_WIKI_PATH = "integrations/alshival-sdk-self-hosted-log-ingest"
DEFAULT_WIKI_TITLE = "Alshival SDK Handoff for Self-Hosted Log Ingest"
DEFAULT_WIKI_BODY_MARKDOWN = """# Alshival SDK Handoff for Self-Hosted Log Ingest

Repository: https://github.com/Alshival-Ai/alshival

## Scope

Alshival Python SDK (`alshival`) integration with our self-hosted platform cloud log ingest.

## Current Status

- Compatible for cloud log ingest on self-hosted deployments.
- SDK posts to: `/u/<owner>/resources/<resource_uuid>/logs/`.
- Backend routes available:
  - `POST /u/<username>/resources/<resource_uuid>/logs/`
  - `POST /team/<team_name>/resources/<resource_uuid>/logs/` (supported by backend, not currently used by SDK routing)

## Server-Side Contract (Confirmed)

### Auth

- `x-api-key` header: required
- `x-user-username` header: recommended/expected for personal keys

### Body

- JSON payload with `logs` array (SDK sends this shape)
- Typical fields:
  - `resource_id`
  - `sdk`
  - `sdk_version`
  - `logs`: `[{level,message,logger,ts,extra}]`

### Response

- Success: `{"status":"ok","resource_id":"..."}`
- Access failure: `403 {"error":"forbidden"}`
- Owner/resource mismatch: `404 {"error":"invalid_resource"}`

## Recommended SDK Configuration for Self-Hosted

Prefer full resource URL so SDK derives everything correctly:

```bash
ALSHIVAL_RESOURCE=https://<your-domain>/u/<resource-owner>/resources/<resource_uuid>/
```

Required:

```bash
ALSHIVAL_API_KEY=<user api key>
ALSHIVAL_USERNAME=<requesting user>
```

Optional:

```bash
ALSHIVAL_CLOUD_LEVEL=INFO|WARNING|ERROR|ALERT|DEBUG|NONE
```

Only set these when not using `ALSHIVAL_RESOURCE`:

```bash
ALSHIVAL_BASE_URL=https://<your-domain>
ALSHIVAL_PORTAL_PREFIX=/DevTools   # only if your deployment is actually mounted there
```

## Known Behavior / Gotchas

- SDK currently routes ingest through `/u/.../logs/`, not `/team/.../logs/`.
- If owner/resource in URL does not match real ownership or alias mapping, backend returns `invalid_resource` (`404`).
- If API key is valid but user identity lacks resource access, backend returns `forbidden` (`403`).
- On `.ai` legacy host patterns, SDK may infer `/DevTools`; validate whether prefix is needed on self-host.

## Quick Validation Checklist

1. Export env vars: `ALSHIVAL_RESOURCE`, `ALSHIVAL_USERNAME`, `ALSHIVAL_API_KEY`.
2. Run:

```python
import alshival
alshival.log.info("sdk connectivity test")
```

3. Verify HTTP `200` from ingest endpoint and that the log appears in the resource Cloud Logs view.
4. Send one `ERROR` message and confirm alert routing (app/SMS/email) based on user notification settings.

## Code Locations (Platform)

- Endpoint routes: `/home/data-team/Fefe/dashboard/urls.py:54`
- Ingest handler: `/home/data-team/Fefe/dashboard/views.py:7781`
- Auth logic: `/home/data-team/Fefe/dashboard/request_auth.py:172`
- Log persistence: `/home/data-team/Fefe/dashboard/resources_store.py:1656`
"""


def seed_default_sdk_handoff_wiki(apps, schema_editor):
    WikiPage = apps.get_model("dashboard", "WikiPage")
    workspace_scope = getattr(WikiPage, "SCOPE_WORKSPACE", "workspace")

    WikiPage.objects.get_or_create(
        scope=workspace_scope,
        resource_uuid="",
        path=DEFAULT_WIKI_PATH,
        defaults={
            "title": DEFAULT_WIKI_TITLE,
            "is_draft": False,
            "body_markdown": DEFAULT_WIKI_BODY_MARKDOWN,
            "body_html_fallback": "",
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0023_userinvite"),
    ]

    operations = [
        migrations.RunPython(seed_default_sdk_handoff_wiki, migrations.RunPython.noop),
    ]
