# redesign-v1 Review — Implementation Plan

## Guiding principles
- Fail fast: no silent None defaults, no defensive optional chaining
- Auth always on: remove `auth_disabled` flag
- All changes grouped by file to minimize touch count

## Changes by file

### houses/config.py
1. Remove `auth_disabled` field from `Settings`
2. Keep `session_secret` default of `""` — startup validation enforces non-empty in auth mode

### houses/server.py
3. Add `_validate_config()` called from `lifespan`:
   - `GOOGLE_CLIENT_ID` must be set → RuntimeError
   - `GOOGLE_CLIENT_SECRET` must be non-empty → RuntimeError
   - `SESSION_SECRET` must be non-empty → RuntimeError
4. Add `close_db()` call in lifespan shutdown

### houses/web/auth.py
5. Normalize email: store `.casefold()` form in session cookie at login
6. `_lookup_person_by_email()`: use `.casefold()` on both sides
7. `_set_session_cookie` / `_clear_session_cookie`: check `request.headers.get("x-forwarded-proto") == "https"` as fallback for Secure flag
8. Add `_STATE_MAX_AGE = timedelta(minutes=10)` + sweep stale state tokens in login endpoint
9. Remove `settings.auth_disabled` branches

### houses/web/api_router.py
10. Replace raw body access with `CommentBody(BaseModel)`:
    ```python
    class CommentBody(BaseModel):
        rid: str
        text: str = Field(..., min_length=1)
        person: str | None = None

        @field_validator("text")
        @classmethod
        def not_blank(cls, v: str) -> str:
            stripped = v.strip()
            if not stripped:
                raise ValueError("text must not be whitespace-only")
            return stripped
    ```
11. Use `.casefold()` for email comparison in inline person lookup (comment attribution)

### houses/database.py
12. No new constraints needed on `comments` table — `BEGIN IMMEDIATE` covers the race
13. `close_db()` kept but now actually called from server.py shutdown

### houses/comments.py
14. `migrate_old_comments()`: `BEGIN` → `BEGIN IMMEDIATE`

### houses/nodes/area.py
15. `TownNode.provenance_source_type`: `SourceType.GEOCODE` → `SourceType.CALC`

### houses/nodes/rail_fare_node.py
16. Move docstring before `provenance_source_type` property

### houses/nodes/monthly_mortgage_payment_node.py
17. Update stale comment: "non-child persons" → "all persons"

### houses/frontend/src/services/api.ts
18. Replace `window.location.href = '/login'` with `router.push('/login')`. Import `router` from `../router`.

### houses/frontend/src/stores/auth.ts
19. Remove `authAvailable` ref
20. `_doCheck()`: on non-2xx, log error, keep current `user` state. No `authAvailable` to toggle.

### houses/frontend/src/router/index.ts
21. Router guard: remove `authAvailable` checks. `user` determines access.

### houses/frontend/src/views/LoginPage.vue
22. Remove "Authentication is not configured" branch entirely. Only states: loading, sign-in button, error from callback.
23. Add `!auth.loading` guard to prevent flicker.

### houses/frontend/src/components/Header.vue
24. Replace hardcoded person list with API fetch from `GET /api/auth/persons`. Only persons with `email != ""`.
25. Backend: add `GET /api/auth/persons` endpoint returning names of persons with non-empty email.

### houses/frontend/src/components/NotesSection.vue
26. Add scoped CSS for `.detail-section` and `.detail-section__title` (matching other sections' patterns)
27. Add `commentError` ref + error display. Set on catch, clear on success or timeout.
28. Remove `settings.auth_disabled` / `authAvailable` guards

### houses/frontend/src/components/PropertyCard.vue
29. No change needed — fail-fast `data.epc.value.band` without `?.` is correct per our principles.

### houses/frontend/src/views/__tests__/PropertyDetail.test.ts
30. Move `import * as api from '../../services/api'` to top of file

### houses/frontend/package.json
31. No change — `vite build` stays in `test` script

### tests/unit/test_attempt.py
32. Add three test cases for falsy values: `value=0`, `value=False`, `value=""`
33. Strengthen `test_non_json_value_stringified` assertion (check `startswith("<")`)

### tests/unit/test_auth.py
34. Add happy-path test: non-superuser with linked email posts comment → 200

### tests/unit/dag/test_architecture.py
35. Make first test's layer paths consistent: drop `./` prefix → `"dag/*.py"`, `"tests/unit/dag/*.py"`

### tools/migrate_comments.py
36. `count_comments_in_db`: accept `conn` parameter instead of opening own connection

### ux-review-and-wireframe.html
37. `::root` → `:root`, `::focus-visible` → `:focus-visible`
