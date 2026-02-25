# DEV

## Prerequisites
- Python 3.13
- Node.js 18+ (only if working on the optional Vite frontend)

## Initial Setup
1. Create and activate a virtual environment:
```bash
python3.13 -m venv .venv
source .venv/bin/activate
```
2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Configure `.env`:
```env
HOST=127.0.0.1
PORT=8000
APP_BASE_URL=http://127.0.0.1:8000
ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000
ALSHIVAL_SSH_KEY_MASTER_KEYS=<random-secret>
```
4. Run migrations:
```bash
python manage.py migrate
```

## Run Locally
- Django server:
```bash
python manage.py runserver
```
- First-run setup page:
```text
/setup/
```
- Login route:
```text
/accounts/login/
```

## Frontend (Optional)
- Start Vite dev server:
```bash
cd frontend
npm install
npm run dev
```
- Build frontend assets into Django static files:
```bash
cd frontend
npm run build
```

## Useful Checks
- Django system checks:
```bash
python manage.py check
```
- Create/apply new migrations:
```bash
python manage.py makemigrations
python manage.py migrate
```

## Project Layout
- Django project settings: `alshival/settings.py`
- App code: `dashboard/`
- URL routing: `alshival/urls.py`, `dashboard/urls.py`
- Main templates: `dashboard/templates/`
- App CSS/JS: `dashboard/static/css/app.css`, `dashboard/static/js/`
- Per-user data root: `var/user_data/`
- SQLite DB: `var/db.sqlite3`

## Recent Dev Notes
- Resource details page includes a header alert icon and persisted alert settings form.
- Resource header includes copy-to-clipboard `ALSHIVAL_RESOURCE=<absolute-resource-url>`.
- Notes on resource details now render user avatars when available from social account metadata.
- API keys list in resource details now fills available card height and scrolls internally.
- Superuser Ask Alshival shell now resolves to per-user home directories under:
  - `USER_DATA_ROOT/<slug-username>-<user_id>/home`
  - created on first shell launch.
- Resource detail endpoints now support both user and team routes:
  - `/u/<username>/resources/<uuid>/...`
  - `/team/<team_name>/resources/<uuid>/...`
- Canonical route forwarding is implemented via `ResourceRouteAlias`.
- Resource package ownership/moves are tracked by `ResourcePackageOwner` and `transfer_resource_package(...)`.
- Alert defaults are App enabled, SMS/Email disabled.

## Quick Manual QA (Resource Details)
1. Open a resource detail page and verify:
   - Alert icon appears in top-right and opens/closes modal.
   - `ALSHIVAL_RESOURCE=...` button copies expected value.
2. In Notes:
   - Avatar image appears when social profile image exists; otherwise fallback initial bubble.
   - Attachment filename only appears when a file is selected.
3. In Resource API Keys:
   - Key list grows to fill card area and scrolls when many keys are present.
4. In Alert Settings:
   - Toggle Health Alerts and Cloud Log Errors channels.
   - Save and refresh; values persist for current user.
   - Defaults for a new user/resource row are App=true, SMS=false, Email=false.
5. Route alias forwarding:
   - Open a resource via `/u/<owner>/resources/<uuid>/`.
   - Change resource scope to team and reopen old URL.
   - Confirm redirect lands on canonical `/team/<team>/resources/<uuid>/`.

## Branding Rule
- Use `Alshival` in user-facing text. Do not reintroduce legacy `Fefe` naming in UI copy.
