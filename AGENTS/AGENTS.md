# Alshival App Technical Details

## Stack Overview
- Backend: Django (project `alshival`, app `dashboard`)
- Auth: django-allauth (login at `/accounts/login/`)
- Database: SQLite (default Django DB: `var/db.sqlite3`)
- User data storage: per-user SQLite database (`member.db`) under `var/user_data/<slug-username>-<id>/`
- Frontend (optional): Vite + React embedded into Django templates

## Backend Details
- Project settings: `alshival/settings.py`
- URLs: `alshival/urls.py`
- App: `dashboard/`
- Login required globally via `alshival/middleware.py`
- User folder creation on login via `dashboard/signals.py`
- Watchlist storage: `dashboard/watchlist.py`

## Frontend Details
- Base templates: `dashboard/templates/base.html` and `dashboard/templates/vertical.html`
- Sidebar/topbar partials in `dashboard/templates/partials/`
- Matrix background effect scoped to sidebar
- Vite React app (optional) in `frontend/`
  - Vite dev server: `http://localhost:5173`
  - Build outputs to `dashboard/static/frontend/`
  - Django template tags for Vite: `dashboard/templatetags/vite.py`

## Setup and Installation
1. Create venv: `python3.13 -m venv .venv`
2. Activate venv: `source .venv/bin/activate`
3. Install deps: `pip install -r requirements.txt`
4. Configure `.env` keys:
   - `HOST`
   - `PORT`
   - `APP_BASE_URL`
   - `ALLOWED_HOSTS`
   - `CSRF_TRUSTED_ORIGINS`
   - `ALSHIVAL_SSH_KEY_MASTER_KEYS`
5. Run migrations: `python manage.py migrate`
6. Start server: `python manage.py runserver`
7. First-run setup route: `/setup/`

## Running
- Django: `python manage.py runserver`
- React (dev): `cd frontend && npm install && npm run dev`
- React (build): `cd frontend && npm run build`

## Key Routes
- `/` Overview
- `/resources/` Resource monitor and watchlist
- `/accounts/login/` Login (Allauth)
- `/setup/` Two-step initial setup flow
- `/accounts/microsoft/login/` Microsoft OAuth login (allauth)
- `/accounts/github/login/` GitHub OAuth login (allauth)
- `/u/<username>/resources/<uuid>/` User-scoped resource detail route
- `/team/<team_name>/resources/<uuid>/` Team-scoped resource detail route

## Initial Setup Flow
- Setup UI is implemented in `dashboard/templates/pages/setup_welcome.html`.
- Step 1 creates the initial admin user:
  - Required: `admin_username`, `admin_password`, `admin_password_confirm`
  - No admin email field in current flow
  - Username must match GitHub-style constraints (validated client + server side)
- Step 2 handles optional connector configuration:
  - OpenAI: API key input
  - Microsoft Entra: tenant/client/secret + test sign-in CTA
  - GitHub OAuth: client/secret + test sign-in CTA
  - Google/Anthropic: placeholder tiles only (coming soon)
- Setup backend logic is in `dashboard/views.py::setup_welcome`.

## Connector Persistence
- OAuth provider settings are persisted via `allauth.socialaccount.models.SocialApp`.
- Social apps are created/updated and attached to current `Site` (`SITE_ID`).
- Microsoft:
  - Provider id: `microsoft`
  - Saved settings include `tenant` in `SocialApp.settings`
  - Test flow action: `setup_action=test_microsoft` redirects to Microsoft login
- GitHub:
  - Provider id: `github`
  - Saved settings include sign-in scopes in `SocialApp.settings` (`read:user`, `user:email`)
  - Test flow action: `setup_action=test_github` redirects to GitHub login

## Domain and URL Behavior
- `APP_BASE_URL` is used to generate connector callback guidance during setup.
- Callback hints shown in UI:
  - Microsoft: `/accounts/microsoft/login/callback/`
  - GitHub: `/accounts/github/login/callback/`
- `APP_BASE_URL` also auto-augments:
  - `ALLOWED_HOSTS` (hostname)
  - `CSRF_TRUSTED_ORIGINS` (scheme + host origin)

## Branding Guidelines
- Brand name: always use `Alshival` (capital A, lowercase remainder).
- Wordmark style: the stylized `Alshival` wordmark (provided by logo assets) is the primary brand mark.
- Preferred logo asset for UI wordmark placements: `dashboard/static/img/branding/alshival-logo-469x317.png`.
- Icon/square logo asset for compact contexts (favicon, small chips): `dashboard/static/img/branding/alshival-logo-256x256.png`.
- Large/source logo asset: `dashboard/static/img/branding/alshival-logo-1536x1024.png`.
- Do not reintroduce legacy `Fefe` naming in user-facing text, defaults, email templates, or UI labels.
- Do not use text-only fallback initials (`F`/`A`) where the logo image is already used in templates.
- Current primary brand touchpoints in Django templates:
  - `dashboard/templates/base.html` (document title, favicon, loader logo)
  - `dashboard/templates/partials/topbar.html` (topbar logo + wordmark)
  - `dashboard/templates/account/login.html` (auth panel wordmark)
  - `dashboard/templates/pages/setup_welcome.html` (setup panel wordmark)

## UI Guidelines
- Gradient jiggle button pattern (used for setup `Next` and `Complete setup`) should reuse:
  - HTML classes: `setup-next-btn`, `setup-next-btn__label`
  - Jiggle state class: `is-jiggling`
  - Keyframe: `chatbot-jiggle`
- Canonical implementation locations:
  - Button markup + jiggle scheduler JS: `dashboard/templates/pages/setup_welcome.html`
  - Button styling + animation keyframes: `dashboard/static/css/app.css`
- Standard button markup pattern:
  - `<button class="primary-btn setup-next-btn" ...><span class="setup-next-btn__label">...</span></button>`
- Behavior rules:
  - Keep frosted dark rainbow gradient background and white text.
  - Respect reduced-motion via `@media (prefers-reduced-motion: reduce)` by disabling jiggle animation.
  - Use the same visual treatment for multi-step primary CTAs to maintain consistency.

## Notes
- `USER_DATA_ROOT` in settings controls where per-user data lives (default `var/user_data/`).
- `STATIC_ROOT` defaults to `var/staticfiles/` for `collectstatic`.

## Recent Implementation Details (2026-02)

### Resource Details Page
- The `Resource Details` view now provides:
  - `resource_url`: absolute URL to the current resource detail page.
  - `resource_env_value`: `ALSHIVAL_RESOURCE=<absolute-resource-url>`.
- Resource details template updates:
  - Overview panel now contains health graph + health check action.
  - Removed duplicated "Current status" text block and "Resource ID" from overview metadata.
  - "Virtual machine" details card removed from the page layout.
  - Notes card uses Team Comments-style author row with avatar support.
  - Resource API Keys card is positioned in the main details grid next to Notes.
  - Cloud Logs card is rendered as a full-width section.
  - Header includes click-to-copy `ALSHIVAL_RESOURCE=<url>`.
  - Header includes an Alerts icon button that opens the persisted alert settings modal.

### Notes / Team Comments Styling
- Notes now show:
  - Social avatar image when available (from `allauth` social account `extra_data`).
  - Initial-based fallback avatar when no image is available.
- Note attachment filename text is hidden unless a file is selected (no "No file selected." placeholder text).

### Resource Alerts Settings (Persisted)
- Resource details alert settings are persisted in each resource package DB (`resource.db`) table `resource_alert_settings`.
- Settings are per-user-per-resource and currently support:
  - Health Alerts: App/SMS/Email
  - Cloud Log Errors: App/SMS/Email
- Default values are:
  - App: enabled (`true`)
  - SMS: disabled (`false`)
  - Email: disabled (`false`)
- Backend write path:
  - `dashboard/views.py::update_resource_alert_settings`
  - `dashboard/resources_store.py::upsert_resource_alert_settings`

### Resource Route Aliases and Canonical Forwarding
- Resource routes now support alias history via `dashboard.models.ResourceRouteAlias`.
- A resource can be addressed by either:
  - user route `/u/<username>/resources/<uuid>/...`
  - team route `/team/<team_name>/resources/<uuid>/...`
- Canonical route behavior:
  - Every resource has one current alias (`is_current=1`).
  - Older aliases are preserved.
  - Requests to old aliases resolve and are redirected to the current canonical detail route.
- Route resolution/wiring:
  - `dashboard/views.py::_resolve_resource_route_context`
  - Team wrappers: `team_resource_detail`, `team_resource_note_add`, `team_resource_logs_ingest`, etc.
  - URL patterns in `dashboard/urls.py` for both `/u/...` and `/team/...` paths.

### Resource Package Ownership / Asset Transfer
- Resource package ownership is tracked in `dashboard.models.ResourcePackageOwner`.
- Package data is stored in owner-scoped roots:
  - User scope: `var/user_data/<user-slug-id>/resources/<resource_uuid>/`
  - Team scope: `var/team_data/<team-slug-id>/resources/<resource_uuid>/`
  - Global scope: `var/global_data/resources/<resource_uuid>/`
- Ownership transfer and filesystem moves are handled by:
  - `dashboard/resources_store.py::transfer_resource_package`
- Transfers move the package directory and update owner metadata; route aliases keep old routes resolving.

### Health Worker + Cloud Log Transition Events
- Worker command: `python manage.py run_resource_health_worker`
- Discovery behavior:
  - Iterates active users, collects resources via `list_resources(user)`, dedupes by `resource_uuid`.
  - This includes team/global-owned packages because ownership resolution happens in `resources_store`.
- Status transition cloud logs:
  - On `healthy -> unhealthy` and `unhealthy -> healthy`, the worker writes a resource cloud log entry.
  - Implemented in `dashboard/health.py::_log_health_transition` and triggered by `emit_transition_log=True`.
  - Log metadata includes source (`run_resource_health_worker`), method, target, previous/current status, latency, packet loss, and error.

### Team KB Duplication (Alpha Temporary Behavior)
- For team-owned resources, health knowledge documents are written into each active team member's personal KB (`var/user_data/<user>/knowledge.db`) instead of relying on a team KB search path.
- This is a temporary alpha strategy to simplify agent retrieval with user-scoped access.
- Known tradeoff: duplicated vectors/documents across members increases storage.
- Team `knowledge.db` stores are considered inactive in this mode and are pruned during knowledge cleanup.
- TODO: move to non-duplicated team-shared retrieval/federated search and remove per-member duplication.

### Resource API Keys Card Behavior
- API keys list now stretches to fill available card space:
  - Card is a vertical flex container.
  - Key list is scrollable and occupies remaining card height.

### Terminal (Ask Alshival) Local Shell Behavior
- Superuser `Ask Alshival` shell sessions now resolve to a per-authenticated-user home directory by default:
  - Path pattern: `<USER_DATA_ROOT>/<slug-username>-<user_id>/home`.
  - Home directory is created on first launch.
- Shell launch changed to avoid login-profile overrides:
  - Uses `bash --noprofile --norc -i` when bash is available.
- Static local identity env overrides (`WEB_TERMINAL_LOCAL_USERNAME` + `WEB_TERMINAL_LOCAL_HOME`) are only used when:
  - `WEB_TERMINAL_FORCE_STATIC_IDENTITY=1` (or `true/yes/on`).
