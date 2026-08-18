# Phase 1 — Auth & Users

Implementable spec. Read `CLAUDE.md` first for data conventions, the dependency
policy, and the service/error layering rules — this document does not repeat them.

**Goal:** a user can register, log in, stay logged in across a page reload
without re-entering credentials, and log out. Every later phase hangs its
ownership checks off the `CurrentUser` dependency built here.

## Dependencies to add

Backend (`cd backend && uv add …`):

| Package | Purpose |
|---|---|
| `pwdlib[argon2]` | Argon2id password hashing |
| `pyjwt` | Access-token encode/decode |
| `uuid-utils` | UUIDv7 primary keys |

Frontend (`cd frontend && pnpm add …`):

| Package | Purpose |
|---|---|
| `zustand` | Auth session client state |
| `react-hook-form`, `zod`, `@hookform/resolvers` | Form handling and validation |

## Data model

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | UUIDv7, generated in Python |
| `email` | `CITEXT` | unique, not null |
| `hashed_password` | `TEXT` | not null, Argon2id |
| `display_name` | `VARCHAR(80)` | nullable |
| `is_active` | `BOOLEAN` | not null, default true |
| `is_verified` | `BOOLEAN` | not null, default false (unused until Phase 7) |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | via `TimestampMixin` |

### `refresh_tokens`

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | UUIDv7 |
| `user_id` | `UUID` FK → `users.id` | `ON DELETE CASCADE`, indexed |
| `token_hash` | `VARCHAR(64)` | unique, SHA-256 hex of the opaque token |
| `family_id` | `UUID` | indexed; constant across a rotation chain |
| `expires_at` | `TIMESTAMPTZ` | not null |
| `revoked_at` | `TIMESTAMPTZ` | nullable; set on rotation, logout, or reuse |
| `replaced_by_id` | `UUID` FK → self | nullable; the token that superseded this one |
| `user_agent` | `VARCHAR(255)` | nullable, for session listing later |
| `ip` | `INET` | nullable |

**Why SHA-256 and not Argon2 for refresh tokens:** the token is 48 bytes of
`secrets.token_urlsafe` entropy, so there is nothing to brute-force, and lookup
must be a fast indexed equality match. Argon2 is for low-entropy human input.

## Token design

**Access token** — JWT, HS256, signed with `settings.secret_key`, **15 minutes**.
Claims: `sub` (user id), `exp`, `iat`, `jti`, `type: "access"`. Reject any token
whose `type` is not `access`, so a refresh token can never be replayed as an
access token.

**Refresh token** — opaque random string, **30 days**, never a JWT (it must be
revocable server-side).

### Rotation and reuse detection

Every call to `/auth/refresh` revokes the presented token and issues a new one
in the same `family_id`. If a token that is **already revoked** is presented,
treat it as theft: revoke every token in that family and return 401. The
legitimate user is forced to log in again — that is the intended cost.

### Transport

- Refresh token → cookie `refresh_token`: `HttpOnly`, `SameSite=Lax`,
  `Path=/api/v1/auth`, `Secure` in every environment except `local`,
  `Max-Age` matching the token lifetime.
- Access token → **response body only, held in memory** by the frontend. Never
  `localStorage`, never a cookie.
- `SameSite=Lax` means a cross-site POST does not carry the cookie, so the
  refresh endpoint needs no separate CSRF token. Revisit only if we are ever
  forced to `SameSite=None`.

## Endpoints

All under `/api/v1/auth`, tagged `auth`.

| Method | Path | Request | Success | Notes |
|---|---|---|---|---|
| POST | `/register` | `{email, password, display_name?}` | `201` + `TokenResponse` + cookie | Logs the user straight in |
| POST | `/login` | `{email, password}` | `200` + `TokenResponse` + cookie | |
| POST | `/refresh` | cookie only | `200` + `TokenResponse` + rotated cookie | |
| POST | `/logout` | cookie only | `204`, cookie cleared | Revokes the whole family |
| GET | `/me` | bearer token | `200` + `UserResponse` | |

`TokenResponse`: `{access_token, token_type: "bearer", expires_in: 900}`.
`UserResponse`: `{id, email, display_name, is_active, created_at}` — **never**
includes `hashed_password`.

## Security requirements

- **Login failures are indistinguishable.** Always `401 authentication_failed`
  with `"Invalid email or password."`, whether or not the email exists.
- **Constant-time-ish login:** when the email is unknown, still verify the
  submitted password against a dummy hash, so response timing does not reveal
  which accounts exist.
- **Password policy:** minimum 12 characters, maximum 128 (the cap is an Argon2
  DoS guard), enforced in the Pydantic schema.
- **Rate limits:** `/login` 10 per minute per IP, `/register` 5 per hour per IP.
  Phase 1 has no Redis, so use an in-process token bucket and note in the
  docstring that it is per-process; move it to Redis in Phase 5.
- **Inactive users** (`is_active = false`) are rejected at login *and* in
  `get_current_user`, so deactivation takes effect within one access-token
  lifetime rather than at next login.
- Log auth events (`user_registered`, `login_succeeded`, `login_failed`,
  `refresh_reuse_detected`) with `user_id` — never with credentials or tokens.

### Known, accepted trade-off

`POST /register` returns `409` for an already-registered email, which leaks
account existence. The privacy-preserving alternative (always `201`, send a
"someone tried to register" email) needs email delivery, which arrives in
Phase 7. Revisit then.

## Backend work

Create:

- `app/models/user.py`, `app/models/refresh_token.py` — **and register both in
  `app/models/__init__.py`** or Alembic will generate a migration dropping them.
- `app/schemas/auth.py`, `app/schemas/user.py`
- `app/core/security.py` — `hash_password`, `verify_password`,
  `create_access_token`, `decode_access_token`, `generate_refresh_token`,
  `hash_refresh_token`. Pure functions, no DB access, no FastAPI imports.
- `app/repositories/user_repository.py`, `app/repositories/refresh_token_repository.py`
  — these earn their place (lookup by hash, family revocation).
- `app/services/auth_service.py` — owns the transaction boundary and raises
  `AuthenticationError` / `ConflictError`, never `HTTPException`.
- `app/api/v1/routes/auth.py`, registered in `app/api/v1/router.py`.
- `app/api/deps.py` — add `CurrentUser` (bearer token → active `User`).
- One Alembic migration (`make revision m="add users and refresh tokens"`),
  hand-reviewed, with a working `downgrade()`.

## Frontend work

- `src/features/auth/` with `api/` (mutation hooks), `routes/LoginPage.tsx` and
  `RegisterPage.tsx`, `components/` (form fields), `store/authStore.ts`.
- **Zustand auth store** holding the in-memory access token and current user.
  It must not persist the token to `localStorage`.
- **`src/lib/api/client.ts` gains a refresh middleware:** on `401`, call
  `/auth/refresh` once and replay the original request. Concurrent 401s must
  share a **single in-flight refresh** (a module-level promise), not fire one
  refresh each. If the refresh fails, clear the store and redirect to `/login`.
- `AuthGuard` layout route wrapping every authenticated route; unauthenticated
  users are redirected to `/login` with the attempted path preserved.
- On app boot, attempt one silent refresh before rendering, so a reload does not
  bounce a logged-in user to the login page.
- Reachable routes: `/login`, `/register`; the dashboard becomes protected.
- Run `make gen-api` and commit `schema.ts` once the routes exist.

## Testing

This phase introduces the **database test fixtures** the whole project will use:

- Session-scoped: create the test database and run `alembic upgrade head` once.
- Function-scoped: open a connection, begin a transaction, bind the session to
  it, roll back after the test. No truncation, no re-migration.
- Fixtures: `db_session`, `client`, `auth_client` (registered user + token).
- Override `get_db` via `app.dependency_overrides` so routes use the test session.

Required cases:

- Password hashing round-trips; a wrong password fails; two hashes of the same
  password differ (salted).
- Access token encodes and decodes; expired token rejected; a refresh token
  presented as an access token is rejected.
- Register → login → `/me` happy path; duplicate email → `409`.
- Login with an unknown email and with a wrong password return **identical**
  status and body.
- Refresh rotates the token and invalidates the old one.
- **Reuse detection:** presenting an already-rotated token revokes the whole
  family, and the newest token then fails too.
- Logout revokes the family and clears the cookie.
- Inactive user is rejected at login and at `/me`.

**Cross-user authorization harness.** Build the parameterized fixture here that
asserts user A cannot read or mutate user B's resources. Phase 1 has only `/me`,
so it starts nearly empty — but every later phase adds its resources to that one
list, which is why it must exist now. Prefer `404` over `403` for another user's
resource, so the API does not confirm the row exists.

## Out of scope

Email verification, password reset, OAuth/social login, MFA, session-listing UI,
and Redis-backed rate limiting. The email sender is stubbed behind an interface
now (console implementation) so Phase 7 can swap in a real provider.

## Definition of done

- [ ] `make check` green; `make migration-check` reports no drift.
- [ ] Migration round-trips: `alembic downgrade base` → `upgrade head`.
- [ ] Register → login → reload page → still authenticated → logout, in a browser.
- [ ] Access token expiry triggers exactly one silent refresh, not one per request.
- [ ] Reuse detection verified by test.
- [ ] `frontend/src/lib/api/schema.ts` regenerated and committed.
- [ ] `docs/ROADMAP.md` Phase 1 marked complete; `CLAUDE.md` updated if any new
      convention was settled.
