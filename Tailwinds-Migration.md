# Tailwind + shadcn/ui Migration Plan

## Goal
Migrate the current Django-template-based UI to a Tailwind-based system using `shadcn/ui` components with minimal disruption, while keeping Django backend routes and business logic intact.

## Current State (Repo-Specific)
- Main UI is server-rendered Django templates.
- Global styling lives in a large legacy stylesheet (`dashboard/static/css/app.css`).
- Existing frontend React app is present but minimal (`frontend/`), which is a good insertion point for Tailwind + `shadcn/ui`.
- Several JavaScript modules depend on current DOM structure and class names, so migration must be incremental.

## Recommended Strategy
Use a hybrid migration:
1. Add Tailwind and `shadcn/ui` in `frontend/`.
2. Migrate page-by-page using React islands mounted inside existing Django templates.
3. Keep Django views/URLs/forms/endpoints unchanged during UI migration.
4. Remove legacy CSS and DOM-coupled JS only after each page is fully cut over.

## Phase 0: Foundation (2-4 days)
1. Add Tailwind + PostCSS config to `frontend/`.
2. Initialize `shadcn/ui`.
3. Establish design tokens mapped to current theme variables.
4. Build base primitives:
   - `Button`
   - `Input`
   - `Card`
   - `Dialog`
   - `DropdownMenu`
   - `Toast`
5. Wire light/dark behavior to existing theme state (`data-theme-mode`).

Exit criteria:
- Tailwind utilities render correctly in the app.
- At least one `shadcn/ui` component renders inside a Django page.

## Phase 1: Integration Layer (3-5 days)
1. Standardize React mount points in templates (`#react-root` per island).
2. Define reliable server->client data passing (JSON script tags or `data-*` attributes).
3. Add a small frontend API helper that handles CSRF and auth-safe requests.
4. Keep old template markup as fallback for first deployment.

Exit criteria:
- A React island loads backend data and performs at least one write action safely.

## Phase 2: Pilot Page (5-8 days)
1. Select one low/medium complexity page (recommended: settings or team directory).
2. Rebuild the page with Tailwind + `shadcn/ui`.
3. Reuse existing backend endpoints and form semantics.
4. Validate responsive behavior, keyboard navigation, and visual parity.

Exit criteria:
- One full page ships in production without behavior regressions.

## Phase 3: Core Shell Components (1-2 weeks)
1. Migrate shared shell UI:
   - topbar
   - sidebar
   - notifications
   - modal patterns
2. Consolidate repeated patterns into shared components.
3. Standardize spacing/type/interaction tokens and ban new ad-hoc legacy styles.

Exit criteria:
- Shared layout and controls are componentized and reused across migrated pages.

## Phase 4: Page Rollout (2-6 weeks)
1. Migrate remaining pages by complexity and business impact.
2. Remove page-specific legacy CSS and JS after each cutover.
3. Maintain a migration tracker with owner, scope, and status for each page.

Exit criteria:
- All target pages use Tailwind + `shadcn/ui`.

## Phase 5: Cleanup and Hardening (3-5 days)
1. Delete dead CSS selectors and obsolete JS hooks.
2. Add linting/style guardrails for component and utility usage.
3. Perform final accessibility and cross-device QA pass.

Exit criteria:
- Legacy styling layer is minimal or intentionally retained only where required.

## Effort Estimate
- Incremental hybrid migration: **4-8 weeks** (1 full-time engineer).
- Full React-first rewrite across all UI behavior: **8-12+ weeks**.

## Primary Risks
- DOM-dependent JavaScript breaks when markup/classes change.
- Theme/token mismatch between legacy CSS variables and new design tokens.
- Temporary inconsistency while old and new UI coexist.

## Mitigation
1. Migrate one page at a time behind flags/toggles when needed.
2. Keep backend contracts stable during UI work.
3. Freeze net-new legacy CSS once migration starts.
4. Require regression checks (visual + behavior) at each cutover.

## Suggested Execution Order
1. Foundation setup in `frontend/`.
2. One pilot page from `dashboard/templates/pages/`.
3. Shared shell components.
4. Remaining pages by complexity.
5. Final cleanup and enforcement.

