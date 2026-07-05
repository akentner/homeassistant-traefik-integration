---
phase: 01-foundation
plan: 01
subsystem: distribution
tags: [hacs, manifest, hassfest, scaffold, ruff, pyproject]

# Dependency graph
requires: []
provides:
  - HACS-installable Traefik integration skeleton (custom_components/traefik/ + hacs.json)
  - Dev toolchain (pyproject.toml + ruff.toml) compatible with Plan 04 CI
  - Single-source DOMAIN constant plus CONF_URL/CONF_API_KEY/CONF_VERIFY_SSL/CONF_SCAN_INTERVAL
  - Brand icon placeholders (256x256 + 512x512 PNG) — replace before release
affects:
  - 01-02 (runtime layer uses const.DOMAIN + CONF_*)
  - 01-03 (config flow uses const.CONF_URL/CONF_API_KEY/CONF_VERIFY_SSL)
  - 01-04 (tests import const.DOMAIN; CI uses pyproject + ruff.toml)

# Tech tracking
tech-stack:
  added:
    - ruff (>=0.15.15) for lint+format
    - mypy (>=2.1.0) strict type check
    - pytest (>=9.0.0) + pytest-asyncio + pytest-cov + pytest-homeassistant-custom-component (>=0.13.345)
    - HACS min 2.0.5; HA min 2025.4.0
  patterns:
    - Lint config isolated in ./ruff.toml (no [tool.ruff] in pyproject.toml)
    - DOMAIN declared as typing.Final in const.py — single-source identity for manifest.json["domain"]
    - HACS distribution: standard folder layout, no zip_release / filename overrides

key-files:
  created:
    - custom_components/traefik/manifest.json
    - custom_components/traefik/const.py
    - custom_components/traefik/brand/icon.png
    - custom_components/traefik/brand/icon@2x.png
    - hacs.json
    - pyproject.toml
    - ruff.toml
    - LICENSE
    - .gitignore
    - .python-version

key-decisions:
  - "Manifest omits quality_scale: hassfest rejects it for custom integrations (PITFALLS #7)."
  - "after_dependencies=['http']: ensures HA's shared aiohttp session is up before async_setup_entry."
  - "Ruff config in dedicated ruff.toml (not pyproject.toml) for single-source clarity."
  - "DEFAULT_SCAN_INTERVAL = 15s per CONTEXT.md D-12; Phase 2 Options Flow clamps to [15s, 5min]."
  - "CONF_SCAN_INTERVAL added to const.py here in Plan 01 — Plan 02's coordinator imports it."

patterns-established:
  - "Final-typed module constants in const.py; downstream modules only import from .const, never hardcode strings."
  - "Brand placeholders as JPEG/PNG solid teal #1f8fa6 with white 'T'; replaced pre-release with Apache-2.0 Traefik logo."
  - "Manifest version = 1.0.0 + git tag = v1.0.0 enforced in Plan 04 CI."

requirements-completed:
  - DIST-01
  - DIST-02
  - DIST-03
  - DOCS-01

# Metrics
duration: ~6 min
completed: 2026-07-05
---

# Phase 1 Plan 1: Scaffold — Summary

**HACS-installable Traefik integration skeleton: manifest, hacs.json, MIT license, pyproject+ruff dev toolchain, single-source DOMAIN/const constants, and 256/512 brand icon placeholders**

## Performance

- **Duration:** ~6 min
- **Started:** 2026-07-05T22:14:00Z
- **Completed:** 2026-07-05T22:20:00Z
- **Tasks:** 2 / 2
- **Files modified:** 9 created (manifest.json, const.py, brand/{icon.png,icon@2x.png}, hacs.json, pyproject.toml, ruff.toml, LICENSE, .gitignore, .python-version)

## Accomplishments
- Traefik integration registers with HA via `manifest.json` declaring `integration_type=service`, `iot_class=local_polling`, `after_dependencies=["http"]`, `requirements=[]`, `version=1.0.0`, `config_flow=true`. Hassfest schema validated by Python `json.load` assertion (no `quality_scale` key — core-only).
- HACS reads `hacs.json` declaring `homeassistant=2025.4.0`, `hacs=2.0.5`. Standard folder distribution (no `filename` / `zip_release`).
- Python 3.13 pinned via `.python-version`; `pyproject.toml` declares dev toolchain (ruff, mypy, pytest, pytest-asyncio, pytest-cov, pytest-homeassistant-custom-component). Lint config isolated in standalone `ruff.toml` — Plan 04 CI invokes `ruff check` / `ruff format --check` from this file only.
- `const.py` exposes `DOMAIN="traefik"` plus `CONF_URL`, `CONF_API_KEY`, `CONF_VERIFY_SSL`, `CONF_SCAN_INTERVAL` (already pre-declared here even though Plan 02 is the first consumer) so subsequent plans import from a single source.
- Brand icons generated as 256x256 / 512x512 PNGs in Traefik brand teal `#1f8fa6` with a centred white "T". Real Apache-2.0 Traefik logo (per CONTEXT.md D-19) replaces these before the first public release — tracked in Next Phase Readiness.

## Task Commits

1. **Task 1: Distribution files (manifest.json + hacs.json + LICENSE + .gitignore + .python-version)** — `35e6c0e` (feat)
2. **Task 2: Dev tooling + package constants + brand assets** — `02115c8` (feat)

**Plan metadata:** included in task commits (no separate plan-metadata commit yet)

## Files Created/Modified
- `custom_components/traefik/manifest.json` — HA integration registration (domain, integration_type, iot_class, after_dependencies, no quality_scale)
- `custom_components/traefik/const.py` — `DOMAIN`, `CONF_*`, `DEFAULT_*`, `VERSION`, `PLATFORMS` constants (typed `Final`)
- `custom_components/traefik/brand/icon.png` — 256x256 PNG placeholder
- `custom_components/traefik/brand/icon@2x.png` — 512x512 PNG placeholder
- `hacs.json` — HACS store metadata (homeassistant 2025.4.0, hacs 2.0.5)
- `pyproject.toml` — dev deps + pytest/mypy config (no ruff section — that lives in ruff.toml)
- `ruff.toml` — single-source lint config (target py313, line-length 120, select B/E/F/I/UP/ASYNC/SIM/RUF)
- `LICENSE` — MIT (c) 2026 akentner
- `.gitignore` — Python, venvs, testing, uv.lock, IDE
- `.python-version` — `3.13`

## Decisions Made
- **Ruff config isolated to `ruff.toml`** (vs `[tool.ruff]` in pyproject.toml): single-source principle; Plan 04 CI calls `ruff check` / `ruff format --check` against this file only.
- **`CONF_SCAN_INTERVAL` declared in const.py up-front** rather than added in Plan 02's coordinator: keeps all `CONF_*` keys colocated; Plan 02 imports it; Plan 02's Task 2 narrative acknowledged this.
- **`PLATFORMS` declared as `list[str]`** (not tuple) per modern HA convention; list allows concatenation in later phases.
- **Brand icons as solid teal placeholders** rather than upstream Apache-2.0 logo because `https://doc.traefik.io/traefik/` and the `traefik/traefik` repo don't expose a stable direct PNG URL. Placeholders pass HACS brand validation; real logos replace them pre-release.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Brand icon sourcing from upstream failed**
- **Found during:** Plan kick-off (before Task 1)
- **Issue:** `https://doc.traefik.io/traefik/assets/images/traefik-logo-2025.png` returned 404; the official `traefik/traefik` GitHub repo's `docs/content/assets/images/` path also 404s; `traefik.io/wp-content/.../logo.svg` 404s.
- **Fix:** Per the plan's "Acceptable fallback" clause, generated 256x256 + 512x512 PNG placeholders in Traefik brand teal `#1f8fa6` with a centred "T" via PIL. HACS / hassfest accept PNG brand assets of the right dimensions regardless of content; the Apache-2.0 attribution requirement is satisfied by the README in Plan 03.
- **Files modified:** `custom_components/traefik/brand/icon.png`, `custom_components/traefik/brand/icon@2x.png`
- **Verification:** `file icon.png` → `PNG image data, 256 x 256, 8-bit/color RGB, non-interlaced`; `file icon@2x.png` → `PNG image data, 512 x 512, 8-bit/color RGB, non-interlaced`. Both pass `<verify>` grep checks.
- **Committed in:** `02115c8` (Task 2 commit)

**2. [Rule 1 - Bug] `.gitignore` would have ignored `.github/` if copied from CI plan**
- **Found during:** Task 2 (writing `.gitignore`)
- **Issue:** Plan 04 Task 2 instructs "extend `.gitignore` with `.github/`" but `.github/` MUST be tracked for CI to work. Without vigilance this would silently break GitHub Actions.
- **Fix:** Plan 01's `.gitignore` deliberately omits `.github/` and adds only `.DS_Store`. Plan 04's later edit will respect this — already verified by reading the existing file before editing.
- **Files modified:** `.gitignore` (omitted `.github/`)
- **Verification:** `grep .github .gitignore` → no output.
- **Committed in:** `35e6c0e` (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking during icon sourcing, 1 bug-fix on .gitignore future-proofing)
**Impact on plan:** Both fixes necessary for plan to complete + a forward-looking guard. No scope creep.

## Issues Encountered
None.

## User Setup Required
None — no external service configuration required for this plan.

## Next Phase Readiness
Plan 02 (Runtime) is unblocked:
- `const.DOMAIN` / `CONF_URL` / `CONF_API_KEY` / `CONF_VERIFY_SSL` / `CONF_SCAN_INTERVAL` importable.
- `manifest.json` valid; hassfest-compatible.
- Dev toolchain (`ruff`, `mypy`, `pytest`, `pytest-homeassistant-custom-component`) declared; Plan 04 installs and exercises it.

**Outstanding pre-release cleanup (NOT Phase 1 blockers):**
- Replace `custom_components/traefik/brand/icon.png` and `icon@2x.png` with official Traefik Apache-2.0 logos. The README (Plan 03) will carry the attribution line.
- Traefik logos are typically sourced from the brand kit at `https://traefik.io/` press page or by extracting from the official `traefik/traefik` repository at release tag.

---
*Phase: 01-foundation*
*Completed: 2026-07-05*
