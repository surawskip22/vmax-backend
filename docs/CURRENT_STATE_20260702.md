# CURRENT_STATE_20260702 - Pan Majster

## Source of truth

This checkpoint supersedes the older June 17 planning package as the active project map.

- branch: `pan-majster`
- HEAD at checkpoint start: `2bdd6cf fix: retry offline queue sync on app resume`
- expected dirty state: `?? docs/MVP_AUDIT_20260618.md`
- `docs/MVP_AUDIT_20260618.md` remains untracked unless explicitly accepted later
- GPT/ChatGPT roadmap notes are useful for product direction, but repo HEAD and current docs are the source of truth

## Current product state

### Stable MVP surfaces

- Core role/access rules are still sacred: no role should gain another role's panel by accident.
- `samodzielny_majster` has the strongest current MVP flow:
  - own jobs
  - job detail
  - progress history with media/audio/problem entries
  - client link `/c/...`
  - reports
  - settings
  - local MVP portfolio/business card
- `company_worker` has a field-work MVP:
  - `Moje zlecenia`
  - `Ustawienia`
  - simple/advanced mode scoped per user
  - start / add progress / finish / reopen actions according to job status
  - scoped offline queue after `7e55b1a` and retry-on-resume after `2bdd6cf`
- `investor` has the new private workspace direction:
  - `Inwestycje / Zlecenia`
  - `Wykonawcy`
  - reports
  - project detail UI
  - discovery/job-posting scaffold marked as future UI
- `company_owner` has received the new workspace-style pass, but still needs targeted visual QA.
- Demo admin/reset exists and is intended only for demo/staging data.

### Future/scaffold surfaces

- Investor discovery and `Oglos zlecenie` are UI scaffolds, not a real marketplace yet.
- Portfolio/business card is still partly local MVP and not a finished backend-backed public product.
- Backend transcription is not the current default flow; live browser/Web Speech fallback remains beta.
- Offers, estimates, contracts, payments, invoices, Android/native app, and marketplace are later roadmap items.

## Role smoke checklist

Use this before larger changes, Render smoke, or external testing.

### Szef firmy / company_owner

- Sees company workspace navigation, including `Majstrowie i ekipy`.
- Does not use investor wording such as `Wykonawcy` for internal team management.
- Can create/open/edit company jobs as before.
- Can manage workers/teams as before.
- Reports open without breaking project navigation.
- New workspace style does not collapse into a mobile-only layout on desktop.

### Inwestor

- Sees `Inwestycje / Zlecenia`, `Wykonawcy`, `Wyszukaj wykonawce`, `Oglos zlecenie`, `Raporty`, `Ustawienia`.
- Does not see `Majstrowie i ekipy`.
- Private investment/job detail works in simple and advanced mode.
- Discovery/job-posting screens clearly behave as future/scaffold UI when no backend exists.
- Reports and client/contractor links do not grant extra permissions.

### Samodzielny majster

- Sees own jobs, reports, portfolio/business card, and settings.
- Can add progress with the intended flow: photo/audio/description/problem.
- Client link `/c/...` shows public client view only.
- Client can comment where supported, but cannot access panel actions.
- Reports are visible/openable without triggering PDF 502/OOM regressions.
- Mobile has no horizontal overflow.

### Company worker / majster firmy

- Sees only `Moje zlecenia` and `Ustawienia`.
- Does not see people/team/company management, portfolio, payments, or owner settings.
- Status actions match job state:
  - assigned: start work
  - in_progress: add progress and finish work
  - completed: completed state and reopen where allowed
- Stage changes are separate from status changes.
- Offline queue badge and sync are scoped to the active user/link.

### Public client `/c/...`

- Shows public project summary/history/reports only.
- Does not expose app shell or worker/owner/investor actions.
- Public comments/problem-intent flow remains limited to the client surface.

### Link-only worker `/g/...`

- Must remain a limited link-only flow.
- Must not receive the full app shell or persistent role panel by accident.
- Needs a focused UI audit before being treated as visually finished.

## Technical risks to keep visible

- `App.tsx` and `panmajster/api.py` remain large. Do not split them broadly without a concrete safety reason.
- Any new browser storage key must be scoped by active user, role, project, or guest token.
- Offline queue fixes are recent and should be part of smoke when changing progress/media flows.
- PDF generation previously hit Render memory limits. Keep generation guarded and avoid auto-opening heavy PDF responses.
- Service worker cache can still make deploy/smoke confusing if stale assets are served.
- Do not treat future UI scaffolds as working backend modules.

## Recommended next sequence

1. Run current-state smoke against the active HEAD.
2. Fix only blockers found by smoke.
3. Do a targeted `company_owner` UI QA pass or `/g/...` link-only audit, one at a time.
4. After core roles are stable, choose one larger direction:
   - backend portfolio/public business card,
   - backend transcription,
   - investor marketplace/discovery backend,
   - offers/estimates.
5. Keep payments, invoices, contracts, native Android/iOS, and large router splits for later.

## Working rules

- One task, one narrow scope, one commit.
- Diagnose from repo and current behavior before following external prompt text.
- Do not mix UI polish, backend changes, PDF, auth, and portfolio in one step.
- If an instruction conflicts with role/access rules, stop and call it out.
- Prefer small role-specific fixes over creative global redesigns.
