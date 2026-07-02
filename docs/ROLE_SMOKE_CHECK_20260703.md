# Role Smoke Check - 2026-07-03

## Scope

This checkpoint records the current role and flow state after the cleanup, offline queue, PDF, portfolio, investor, independent contractor, and company worker work.

No application code was changed for this checkpoint.
No local browser smoke, Render smoke, push, or deploy was performed.

## Git State

- Branch: `pan-majster`
- HEAD before this document: `8be701e fix: refresh offline queue count after sync attempts`
- Expected untracked file left outside this checkpoint: `docs/MVP_AUDIT_20260618.md`
- Local note: `git status` and root-level pytest report permission warnings for old temporary directories such as `tmp_pytest_*`. These are local filesystem artifacts, not tracked application changes.

## Automated Verification

- `python -m pytest` from repo root: blocked during collection by local permission-denied `tmp_pytest_*` directories.
- `python -m pytest tests`: OK, `59 passed`.
- `node frontend/scripts/source-regression.mjs`: OK.
- `tsc -b`: OK.
- `vite build`: OK, with the known bundle-size warning for the main JS chunk.

## Role Checklist

### Company owner / szef firmy

- Navigation remains company-management oriented:
  - `Zlecenia`
  - `Majstrowie i ekipy`
  - `Raporty`
  - `Ustawienia`
- Backend tests still cover project ownership, company worker assignment, worker profile visibility, PDF access, generated reports, and public client links.
- Known debt: some settings and worker UI class names still carry historical naming from worker flows. This is naming/style debt, not a current role-access blocker.

### Investor / inwestor

- Navigation remains investor oriented:
  - `Inwestycje / Zlecenia`
  - `Wykonawcy`
  - `Wyszukaj wykonawce`
  - `Oglos zlecenie`
  - `Raporty`
  - `Ustawienia`
- Discovery and job-posting views are still scaffold/future UI. They must not be treated as a real marketplace until backend models/API are added.
- Investor should not see the company owner's `Majstrowie i ekipy` language.

### Independent contractor / samodzielny majster

- Navigation remains contractor oriented:
  - `Moje zlecenia`
  - `Raporty`
  - `Moja wizytowka`
  - `Ustawienia`
- Current MVP includes the private project flow, reports, public card/portfolio MVP, and settings.
- Independent contractor should not get company team management.

### Company worker / majster firmy

- Navigation is intentionally narrow:
  - `Moje zlecenia`
  - `Ustawienia`
- Source-regression explicitly guards that `company_worker` does not receive the people/team panel.
- Current field workflow includes project list/detail, simple/advanced modes, add progress variants, stage action, start/close/reopen actions according to status, settings, and offline queue badge refresh.

### Public client link `/c/...`

- Backend tests cover:
  - generated ready PDF report visibility and downloads,
  - hidden failed/generating/ready-without-file reports,
  - chronological grouped entries with audio,
  - media-token isolation,
  - client comments and problem confirmation,
  - cover image fallback.
- Public client must remain a public preview, not a full app and not an entry-adding interface.

### Guest worker link `/g/...`

- Guest link remains a project-scoped flow, not the full authenticated application.
- Existing tests cover guest permissions, revocation, generated PDF access restrictions, and foreign-user/public-client blocking.
- Product note from the guest-link audit remains open: guest add/history can currently set entry stage when allowed to add history. This may be intended field behavior or may need a later product decision.

## Current Risks

1. `App.tsx` remains large and feature-dense despite previous extractions. It is not an emergency, but new broad UI changes increase merge and regression risk.
2. Root-level `pytest` is noisy because old local temp directories are unreadable. Running `pytest tests` verifies the application tests cleanly.
3. Marketplace/discovery and job posting are visible future scaffolds. They should stay clearly marked until backend support exists.
4. `/g` stage behavior needs a product decision before tightening or expanding.
5. Bundle size warning remains; this is a future frontend splitting concern, not a blocker for the current smoke.

## Recommendation

The repo is in a good state for another small, scoped step. Before adding a large feature, choose one of:

- decide the `/g` stage-change behavior,
- run a short manual role smoke,
- do a small company-owner UI polish,
- or create a tiny cleanup around naming/style debt that does not touch role logic.

Do not start a broad router split, large App.tsx split, marketplace backend, or auth overhaul as the next automatic step.
