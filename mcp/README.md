# MCP Server

Run locally:

```bash
uvicorn mcp.app:app --host 0.0.0.0 --port 8080
```

Endpoints:

- `GET /health` (no auth)
- `POST /mcp/` (MCP streamable HTTP, requires global API key)
- `GET|POST /github/` (proxied to internal GitHub MCP upstream, requires global API key)

Auth:

- Header: `x-api-key` (or `MCP_API_KEY_HEADER` override)
- Also accepts `Authorization: Bearer <key>`
- Identity headers supported: `x-user-username`, `x-user-email`, `x-user-phone`
- Optional resource scope header: `x-resource-uuid`
- API key auth supports global/account/resource keys:
  - account keys require a resolvable identity (username/email/phone)
  - resource keys are valid only for the specified resource UUID
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

Tools:

- `ping` (dummy tool)
- `search_kb`:
  - input: `query`
  - searches authenticated user's `var/user_data/<user>/knowledge.db` (top 4)
  - searches `var/global_data/knowledge.db` (top 3)
  - returns both buckets and merged results
- `resource_health_check`:
  - input: `resource_uuid`
  - runs live health check using existing resource monitor logic
  - returns status, checked timestamp, target, check method, latency, packet loss, and error
  - access: authenticated user can check global resources plus resources they own or can access via team membership
