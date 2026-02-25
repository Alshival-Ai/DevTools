# Site Loader Technical Documentation

This document explains the full implementation of the Alshival site loader so you can reuse the same pattern in other projects.

## What This Loader Does

The loader is a session-gated branded intro that:

- shows a full-screen overlay with an opaque backdrop
- displays a large logo
- types `Alshival.Ai` with a typewriter effect
- stays visible for a minimum duration
- hides only after:
  - typing is finished
  - page load is complete
  - minimum visible time has elapsed
- fades the app UI in after the loader exits
- runs once per browser tab session (using `sessionStorage`)

## Files Involved

- Template shell: `dashboard/templates/base.html`
- Loader script: `dashboard/static/js/page-loader.js`
- Loader styles: `dashboard/static/css/app.css`

## High-Level Architecture

The loader has 3 layers that coordinate through root `<html>` classes:

1. Early bootstrap (inline `<script>` in `<head>`)
- decides whether intro should run this session
- adds root classes before CSS paints to avoid flashes

2. Visual layer (HTML + CSS)
- full-screen `.page-loader` overlay
- opaque backdrop `.page-loader__backdrop`
- branded content card `.page-loader__card`
- hidden page content `.page-content` until ready

3. Runtime controller (`page-loader.js`)
- runs typewriter animation
- waits for load timing gates
- flips classes to exit loader and reveal UI
- marks session as seen in `sessionStorage`

## State Model (Root Classes)

The implementation uses three root classes:

- `page-loading`
  - means the loader overlay is active and visible
- `page-ready`
  - means loader should fade out
- `ui-visible`
  - means app content is visible/faded in

Optional class:

- `show-session-intro`
  - explicit marker that this navigation should run the session intro logic

## Lifecycle Timeline

1. HTML starts parsing in `<head>`
2. Inline bootstrap checks `sessionStorage.alshival_intro_seen`
3. If not seen:
- adds `page-loading show-session-intro`
- leaves content hidden
4. If seen:
- adds `page-ready ui-visible` immediately
- loader script no-ops quickly
5. `page-loader.js` runs
6. If intro mode:
- starts typewriter
- waits for:
  - `window.load`/`pageshow`
  - minimum visible duration (`minVisibleMs`)
  - typewriter completion
7. On completion:
- sets session flag
- adds `page-ready`
- removes `page-loading show-session-intro`
- adds `ui-visible` in next animation frame
8. Content fades in

## Bootstrap Script (Critical Anti-Flash)

In `base.html` head:

```html
<script>
  (function () {
    var root = document.documentElement;
    var introSeen = false;
    try {
      introSeen = window.sessionStorage.getItem('alshival_intro_seen') === '1';
    } catch (error) {
      introSeen = false;
    }
    if (introSeen) {
      root.classList.add('page-ready', 'ui-visible');
    } else {
      root.classList.add('page-loading', 'show-session-intro');
    }
  })();
</script>
```

Why this is inline:

- it runs before external CSS/JS download
- prevents first-paint flicker where content appears briefly

## Content Visibility Guard

Also in `<head>`, inline critical style:

```html
<style>
  .page-content { opacity: 0; visibility: hidden; }
  html.ui-visible .page-content { opacity: 1; visibility: visible; }
</style>
```

This ensures content is not visible even if main stylesheet is delayed.

## Loader Markup Structure

In `base.html` body:

```html
<div class="page-loader" aria-hidden="true">
  <div class="page-loader__backdrop"></div>
  <div class="page-loader__card">
    <div class="page-loader__logo">
      <img class="page-loader__logo-img" src="..." alt="Alshival logo" />
    </div>
    <div class="page-loader__wordmark" data-typewriter-target="Alshival.Ai"></div>
    <div class="page-loader__label">Developer Enablement Console</div>
    <div class="page-loader__track">
      <div class="page-loader__bar"></div>
    </div>
  </div>
</div>

<main class="page-content">
  ...
</main>
```

Notes:

- overlay is sibling to content, not nested inside it
- backdrop is separate from card for precise layering control
- typewriter text is driven by `data-typewriter-target`

## CSS System

### Typography and Color Spec (Exact Values)

The loader relies on the same global font import used by the app:

```css
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
```

Font usage in loader:

- Typewriter wordmark (`.page-loader__wordmark`)
  - font-family: `'Space Grotesk', system-ui, sans-serif`
  - font-size: `clamp(1.75rem, 5.2vw, 2.75rem)`
  - font-weight: `700`
  - letter-spacing: `0.04em`
  - color: `var(--text)` (`#e8eefc` dark, `#0f172a` light)
- Subtitle (`.page-loader__label`)
  - font-family: `'Space Grotesk', system-ui, sans-serif`
  - font-size: `0.96rem`
  - font-weight: `500`
  - letter-spacing: `0.08em`
  - text-transform: `uppercase`
  - color: `var(--muted)` (`#9db0d5` dark, `#475569` light)
- Cursor color (`.page-loader__wordmark::after`)
  - color: `var(--accent)` (`#4f8cff` dark, `#2563eb` light)

Primary theme tokens used by loader:

- dark theme:
  - `--text: #e8eefc`
  - `--muted: #9db0d5`
  - `--accent: #4f8cff`
  - `--accent-2: #ffb44a`
  - `--border: rgba(148, 163, 184, 0.2)`
  - `--shadow: 0 18px 40px rgba(15, 23, 42, 0.35)`
- light theme (`.light-style`):
  - `--text: #0f172a`
  - `--muted: #475569`
  - `--accent: #2563eb`
  - `--accent-2: #f59e0b`
  - `--border: rgba(15, 23, 42, 0.12)`
  - `--shadow: 0 18px 40px rgba(15, 23, 42, 0.12)`

Backdrop gradients (full opaque base + atmosphere layers):

- dark:
  - `radial-gradient(circle at 20% 10%, rgba(79, 140, 255, 0.24), transparent 45%)`
  - `radial-gradient(circle at 80% 90%, rgba(255, 180, 74, 0.16), transparent 40%)`
  - base fill: `#060b18`
- light:
  - `radial-gradient(circle at 20% 10%, rgba(37, 99, 235, 0.12), transparent 45%)`
  - `radial-gradient(circle at 80% 90%, rgba(245, 158, 11, 0.1), transparent 40%)`
  - base fill: `#f8fafc`

Card visuals:

- width: `min(560px, calc(100vw - 2.5rem))`
- border: `1px solid var(--border)`
- radius: `22px`
- padding: `1.8rem 1.4rem 1.25rem`
- dark background: `rgba(15, 23, 42, 0.75)`
- light background: `rgba(255, 255, 255, 0.9)`
- backdrop blur: `blur(10px)`
- shadow: `var(--shadow)`

Progress bar visuals:

- track:
  - height: `7px`
  - background: `rgba(148, 163, 184, 0.25)`
  - radius: `999px`
- moving bar:
  - width: `35%`
  - background: `linear-gradient(90deg, var(--accent), var(--accent-2))`
  - animation: `page-loader-sweep 1.4s ease-in-out infinite`

Opacity/transitions (loader and content):

- loader hidden default:
  - `opacity: 0`
  - `visibility: hidden`
  - `transition: opacity 320ms ease, visibility 0s linear 320ms`
- loader active:
  - `transition: opacity 220ms ease`
- content fade-in:
  - `opacity` + `transform`
  - `transition: opacity 420ms ease, transform 420ms ease`

### Overlay Visibility

Key rules:

- base `.page-loader` is hidden (`opacity: 0; visibility: hidden`)
- `html.page-loading .page-loader` makes it visible
- `html.page-ready .page-loader` hides it again

### Opaque Backdrop

The backdrop uses a solid base color plus gradients:

- dark mode base: `#060b18`
- light mode base: `#f8fafc`

This guarantees the underlying UI is fully obscured while loader is active.

### Branded Card

Card styling details:

- centered panel with blur and shadow
- responsive width: `min(560px, calc(100vw - 2.5rem))`
- logo and title centered

### Typewriter Cursor

`.page-loader__wordmark::after` renders a blinking `|` cursor with `@keyframes type-cursor`.

### Progress Sweep

`.page-loader__bar` animates continuously with `@keyframes page-loader-sweep`.

### UI Fade-In

`.page-content` starts hidden/offset and animates to visible on `html.ui-visible`.

## JavaScript Controller Logic

Main control file: `dashboard/static/js/page-loader.js`

Core gates:

- `typingDone`
- `pageLoaded`
- `minElapsed`
- `finished` (idempotence guard)

`maybeFinish()` exits loader only when all gates are true.

### Session Gating

- storage key: `alshival_intro_seen`
- value: `'1'`
- persisted in `sessionStorage` (tab-scoped)

If storage is unavailable (private mode restrictions, strict policies), errors are swallowed and intro reruns each navigation.

### Minimum Time

```js
const minVisibleMs = 2600;
```

This controls the cinematic duration floor.

### Typewriter Speed

```js
setInterval(..., 105);
```

Every 105ms, one additional character appears.

### Load Synchronization

Uses both:

- `window.load`
- `pageshow`

`pageshow` helps restore behavior for back/forward cache navigations.

## Accessibility and Motion

Current implementation includes:

- `aria-hidden="true"` on visual loader wrapper (non-interactive decoration)
- `prefers-reduced-motion` handling in JS and CSS:
  - JS: instantly sets full typewriter text
  - CSS: stops progress bar animation

If you want screen-reader announcement support, add a live region outside the hidden overlay and announce loading state there.

## Performance Characteristics

Good properties:

- no framework dependency
- tiny runtime script
- no layout thrash loops
- class-based state changes (GPU-friendly opacity/transforms)
- early inline gating avoids flash-of-content

Potential cost:

- forcing a minimum duration intentionally delays first interaction for new session entries

## Porting To Other Projects

## 1) Add root-state bootstrap in `<head>`

- copy the inline script
- change storage key to your product namespace

## 2) Add critical inline content-hide style

- hide `.page-content` by default
- reveal on `html.ui-visible`

## 3) Add overlay markup near top of `<body>`

- include backdrop, card, logo, wordmark target, progress bar

## 4) Wrap app shell in `.page-content`

- everything that should fade in belongs inside

## 5) Add CSS rules

- visibility rules for `page-loading/page-ready`
- backdrop/card/theme styles
- typewriter cursor animation
- UI fade-in transition

## 6) Add loader controller JS

- gate by session flag
- typewriter routine
- minimum duration timer
- load/pageshow completion events

## 7) Tune project-specific constants

- `introStorageKey`
- `minVisibleMs`
- typewriter interval
- brand text (`data-typewriter-target`)

## Framework-Specific Notes

### Django / server-rendered pages

- this pattern works out-of-the-box with base template inheritance

### React / SPA

- loader typically runs once at app bootstrap
- if you want route-level intros, move state logic into router transitions

### Next.js / Nuxt / Remix

- keep bootstrap script in document/head layout
- ensure server/client hydration does not remove root classes prematurely

## Customization Recipes

### Change typed text

- update `data-typewriter-target` in markup

### Change intro duration

- update `minVisibleMs` in `page-loader.js`

### Make it run every page load (not once/session)

- remove sessionStorage short-circuit in bootstrap and JS

### Make it run once per login (instead of per tab session)

- set/clear a server-side session flag and expose it to template

### Add multi-word typing with pauses

- replace single interval with queued segments and timed delays

## Troubleshooting

### I can still see UI beneath loader

Check:

- `.page-loader__backdrop` has opaque base color
- `.page-content` has `visibility: hidden` before `ui-visible`
- bootstrap script runs before stylesheet link

### Loader never disappears

Check:

- `page-loader.js` loaded successfully
- no JS errors before `maybeFinish()`
- `window.load` fires (look for blocked network requests)

### Intro repeats every navigation

Check:

- browser allows `sessionStorage`
- key name is consistent in both bootstrap and JS

### Theme mismatch in loader

Check:

- theme class (for this project: `.light-style`) is applied on root before/after loader rules

## Security / Privacy Notes

- loader state uses non-sensitive client storage (`sessionStorage`)
- no credentials or user data are stored
- safe to clear at any time; worst case is intro replay

## Suggested Minimal Reusable API (if extracting)

If you turn this into a reusable module, expose:

- `storageKey` (string)
- `minVisibleMs` (number)
- `typewriterMsPerChar` (number)
- `typedText` (string)
- `enableSessionGate` (boolean)
- `onIntroStart` / `onIntroEnd` callbacks

## Current Project Values (Alshival)

- storage key: `alshival_intro_seen`
- typed text: `Alshival.Ai`
- min duration: `2600ms`
- type interval: `105ms`
- fade-out handoff delay: `360ms`
- content fade-in: `420ms`

## Final Notes

This implementation is intentionally simple and robust:

- class-driven state machine
- minimal JS surface area
- strong anti-flash behavior
- session-based replay control
- brand-forward visuals that remain easy to transplant

If you want, a next step is extracting this into a standalone `loader.css` + `loader.js` pair with a tiny initialization API so you can drop it into new repos in under a minute.
