# MCP Server

Run locally:

```bash
uvicorn mcp.app:app --host 0.0.0.0 --port 8080
```

Endpoints:

- `GET /health` (no auth)
- `POST /mcp/` (MCP streamable HTTP, requires global API key)
- `GET|POST /github/` (proxied to internal GitHub MCP upstream, requires global API key)
- `GET|POST /asana/` (proxied to Asana MCP upstream, requires global API key)

Auth:

- Header: `x-api-key` (or `MCP_API_KEY_HEADER` override)
- Also accepts `Authorization: Bearer <key>`
- Identity headers supported: `x-user-username`, `x-user-email`, `x-user-phone`
- Optional resource scope header: `x-resource-uuid`
- API key auth supports global/account/resource keys:
  - account keys require a resolvable identity (username/email/phone)
  - resource keys are valid only for the specified resource UUID
  - when identity headers are omitted, the server attempts to resolve an account/resource key owner from member DB keys
- Twilio phone auth fallback (when API key is missing):
  - requires `X-Twilio-Signature` (configurable via `MCP_TWILIO_SIGNATURE_HEADER`)
  - resolves identity from `x-user-phone` header or Twilio `From` form field
  - validates Twilio signature using configured Twilio auth token
  - if `x-resource-uuid` is provided, resource access is enforced for resolved phone user
- `search_kb` authorization:
  - requires authenticated user identity
  - reads only the authenticated user's personal KB plus global KB

GitHub proxy:

- Configure `MCP_GITHUB_UPSTREAM_URL` (example: `http://github-mcp:8082/`)
- The `/github/*` route forwards method/body/query/headers to that upstream.

Asana proxy:

- Configure `MCP_ASANA_UPSTREAM_URL` (example: `https://mcp.asana.com/v2/mcp`)
- The `/asana/*` route forwards method/body/query/headers to that upstream.
- Include Asana MCP bearer auth in `Authorization` header when calling the proxy.

Tools:

- `ping` (dummy tool)
- `search_kb`:
  - input: `query`
  - searches authenticated user's `var/user_data/<user>/home/.alshival/knowledge.db` (top 4)
  - searches `var/global_data/knowledge.db` (top 3)
  - includes accessible Workspace Wiki matches (top 4)
  - returns both buckets and merged results
- `resource_kb`:
  - input: `resource_uuid`, optional `query`, `limit`
  - searches `resources/<resource_uuid>/knowledge.db` for resource-specific context
  - access: authenticated user can query global resources plus resources they own or can access via team membership
- `resource_health_check`:
  - input: `resource_uuid`
  - runs live health check using existing resource monitor logic
  - returns status, checked timestamp, target, check method, latency, packet loss, and error
  - access: authenticated user can check global resources plus resources they own or can access via team membership
- `alert_filter_prompt`:
  - input: optional `action` (`get|replace|append|clear`), optional `prompt`
  - reads or updates the authenticated user's alert filter prompt stored in `member.db`
  - `append` adds new text to existing prompt; `clear` removes all custom filter instructions
  - access: authenticated user only (self)
- `resource_logs`:
  - input: `resource_uuid`, optional `limit`, `level`, `contains`
  - queries resource log rows from the resource package DB
  - supports simple filtering by exact level and message/logger substring
  - access: authenticated user can query global resources plus resources they own or can access via team membership
- `resource_ssh_exec`:
  - input: `resource_uuid`, `command`, optional `timeout_seconds`, optional `max_output_chars`
  - executes a one-shot SSH command against a VM resource using the resource's configured SSH credentials
  - enforces bounded timeout/output and returns `exit_code`, `stdout`, `stderr`, truncation flags, and execution metadata
  - access: authenticated user can execute on global resources plus resources they own or can access via team membership
- `search_users`:
  - input: optional `query`, `phone`, `limit`
  - searches global Chroma `user_records` collection
  - supports semantic search (`query`) and exact phone match filtering (`phone`)
  - access: superuser only
- `directory`:
  - input: optional `query`, `phone`
  - searches global Chroma `user_records` collection
  - returns top 4 matches
  - access: superuser only
- `sms`:
  - input: `message`, optional `username`, optional `phone_number`
  - sends SMS via Twilio to either the user phone (by `username`) or direct `phone_number`
  - trims message body to 1200 chars
  - access: superuser only
