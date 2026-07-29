# Plan: `redesign-v1` fixes

## Architecture

| # | Change | Notes |
|---|---|---|
| A1 | **Create `houses/database.py`** — single application connection owner. `get_connection()` with WAL + busy_timeout, `init_db()` creates all app tables, `close_db()`. Overridable via `testing` flag. | |
| A2 | **`houses/comments.py` removes connection management.** Delete `_get_db()`, `close_db()`, `init_comments_db()`, `_connection_cache`, `_db_path`. CRUD calls `database.get_connection()`. | |
| A3 | **`dag/persistence.py` unchanged.** Independent connection to its own tables. WAL mode on both connections to same file is standard SQLite practice — kept separate because `dag/` is a reusable library that shouldn't depend on application modules. | Dual connections accepted. |

## Auth & Sessions

Signed cookie design (itsdangerous.URLSafeTimedSerializer):

- **Cookie name:** `session` (new). Old `session_token` cookies ignored gracefully — first request after upgrade sees no session, user re-auths once.
- **Payload:** `{ email, name, picture, is_superuser, impersonating, exp }`
- **Flags:** `HttpOnly=True`, `Secure=True` when HTTPS, `SameSite=Lax`, `max_age=30d`
- **Logout:** Sets empty cookie with `max_age=0`. Signed cookie remains technically valid server-side until `exp` — acceptable for a dev tool.

| # | Change | Notes |
|---|---|---|
| F1 | **Add `AUTH_DISABLED`** to config (env `HOUSES_AUTH_DISABLED`, default `False`). | |
| F2 | **Add `SESSION_SECRET`** to config (env `HOUSES_SESSION_SECRET`). Required when auth enabled. | |
| F3 | **Startup validation:** if `GOOGLE_CLIENT_ID` empty and `AUTH_DISABLED` False, log error + raise. Dev message in logs only. Startup log warning when `AUTH_DISABLED=True`. | No production env guard — project has no env-mode concept, log warning is proportional. |
| F4 | **Middleware gates on `AUTH_DISABLED`** — when disabled, passes all requests through. | |
| F5 | **Replace in-memory `_sessions` with signed cookies.** `get_session_user()` decodes + verifies cookie. `_sessions` dict and `_clear_expired_sessions()` removed. `_oauth_states` stays (ephemeral — OAuth flow only). `_lookup_person_by_email` stays (used in migration). | See design above. |
| F5a | **New endpoint `POST /api/auth/impersonate`.** Accepts `{ person }` (or null to stop). Re-signs cookie with updated `impersonating` field. Survives restarts and page refreshes. | |
| F6 | **Remove dead JWT decode** (`google_jwt.decode(id_token, verify=False)` at line 177). | |
| F7 | **Replace dynamic `__import__("logging")`** with module-level `logger.exception(...)`. | |
| F8 | **URL-encode exception messages** in OAuth callback redirect with `urllib.parse.quote()`. | |

## P1 Bugs

| # | Change | Notes |
|---|---|---|
| F9 | **Swapped `postComment` arguments** — named params `{ rid, text, person? }`. Fix call site in NotesSection.vue. | Compile-time safety against re-occurrence. |
| F10 | **Remove child equity filter** — delete `if not person.is_child` from `MonthlyMortgagePaymentNode.compute()`. `provenance_formula` already sums all persons (no filter) — fix makes them match. | |
| F11 | **Sinking fund yearly→monthly in provenance.** Formula inlines: yearly → ÷12 → ×⅔ (our share). Computation unchanged (`yearly / 12 * 2 / 3` is intentional — ×⅔ because figures are Simon+Lorena's share, excluding Ashby). | |
| F12 | **Concurrent `checkAuth()` race** — store caches in-flight promise in `_pendingCheck`, returns same promise to concurrent callers. | Must be implemented before F14. |
| F13 | **Missing RID validation on comments** — add `get_registry_property(rid)` 404 check to POST and GET endpoints. | |

## P2 Frontend UX

| # | Change | Notes |
|---|---|---|
| F14 | **LoginPage redirect race** — add `watch(auth.user)` redirect to `/` when user becomes non-null after mount. | Depends on F12. |
| F15 | **`logout()` doesn't clear on fetch failure** — `try/finally`, always null `user`, `superuserMode`, `impersonating`. | |
| F16 | **`login()` silently ignores errors** — handle `{ status: "error" }` response, surface message to user. | |
| F17 | **401 handling in API client** — response interceptor redirects to `/login`. Must exclude `/api/auth/*` to avoid redirect loops. | |
| | **Rule:** All user-facing errors get user-focused message visible in UI. Dev-friendly detail logged server-side. | Applied across all changes. |

## P2 Comments Migration

| # | Change | Notes |
|---|---|---|
| F18 | **Migration timestamp** — use `"1980-01-01T00:00:00+00:00"` so migrated entries sort before all real comments. | Format matches `datetime.now(UTC).isoformat()` output. |
| F19 | **Attribution** — `group_notes` → `"Simon"` (was `"Group"`). `ashby_comments` → `"Ashby"` (already correct). | |
| F20 | **Idempotent migration** — single-connection architecture + within-transaction check prevents duplicate rows under concurrency. | |

## Test Infrastructure

| # | Change | Notes |
|---|---|---|
| F23 | **Update test isolation fixtures** — patch `database.get_connection()` alongside `dag.persistence._get_db()` in `tests/unit/isolation_fixtures.py`. Single in-memory DB shared between both connection paths. | Blocking — without this, all existing tests silently target wrong DB. |

## P3 Nits

| # | Change | Notes |
|---|---|---|
| F21 | **Auth headers on all GET endpoints** — add `authHeaders()` to `fetchAllSummaries`, `fetchPropertyDetail`, `fetchComments`, `fetchSettings` for impersonation consistency. | |
| F22 | **CSS lint in `make lint`** — add `cd houses/frontend && npm run lint:css` to the lint target. Already runs in `npm test` (via `make test`) too — not redundant, useful for standalone lint. | |

## Implementation Order

```
Phase 1: Foundation
  F1, F2 → config fields
  F3 → startup validation
  A1 → database.py module
  F7 → module-level logger (standalone)

Phase 2: Database refactoring
  A2 → comments.py → use database.get_connection()
  F23 → update test isolation fixtures
  F18, F19, F20 → migration fixes (depend on A2)

Phase 3: Auth overhaul
  F4 → middleware gating
  F5 → signed cookies + /impersonate endpoint
  F5a → impersonation endpoint
  F6 → dead JWT decode removal
  F8 → URL-encode exceptions

Phase 4: P1 bugs (independent)
  F9 → named params postComment
  F10 → child equity filter removal
  F11 → sinking fund provenance
  F12 → checkAuth race (BEFORE F14)
  F13 → RID validation

Phase 5: Frontend UX (depends on F12)
  F14 → LoginPage watch redirect
  F15 → logout try/finally
  F16 → login error handling
  F17 → 401 interceptor

Phase 6: Nits
  F21 → auth headers on GET
  F22 → CSS lint in make lint
```
