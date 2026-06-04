# Login Page

## Purpose

The login gate protects QuantBase when `QUANTBASE_AUTH_ENABLED=1` while preserving a lightweight demo workflow. It supports administrator account/password login and temporary guest-code login without SMS, email verification, browser-stored JWTs, or self-registration.

## Route

- There is no standalone public app route for login. `AuthProvider` checks `/api/v2/auth/me` on startup; when auth is enabled and no valid session exists, `Login` renders instead of `MainLayout`.
- Visitor deep links may include a hash invite, for example `/#invite=BP-...` or `/#guest_code=BP-...`. The login page reads only `window.location.hash`, auto-selects guest mode, submits the invite through `/api/v2/auth/guest/login`, and clears the hash from the address bar with `history.replaceState`.
- A successful login returns to the original QuantBase shell and keeps the session in a server-side `auth_sessions` row plus an HttpOnly cookie. Administrator login is remembered by default across browser restarts until the operator clicks logout or the server session is revoked.
- If auth is disabled, the app renders the main shell as an admin-compatible local operator.

## First-Screen Layout

- Full-screen dark financial workspace.
- Desktop: left informational rail with QuantBase identity and two compact permission summaries; right side contains the login form.
- Mobile/narrow: form remains the primary first-screen element.
- The form has a segmented control with `访客邀请码` first and `管理员登录` second.

## Interactions

- Guest mode accepts a temporary code such as `BP-...` and submits to `POST /api/v2/auth/guest/login`.
- Guest hash-link mode accepts `/#invite=BP-...` and `/#guest_code=BP-...`; query-string invites such as `?invite=...` are intentionally not supported so the code is not sent as a normal HTTP query.
- Admin mode pre-fills the current deployment's administrator username `Shadowell`, accepts the password, and submits to `POST /api/v2/auth/admin/login`.
- Errors from the backend are shown inline in the form.
- Logout is available from the sidebar when auth is enabled.
- Guest-code sessions expire with the source code, have no separate click-time hours cap, and cannot use the administrator long-lived login behavior.

## Data Sources

- `/api/v2/auth/me`
- `/api/v2/auth/admin/login`
- `/api/v2/auth/guest/login`
- `/api/v2/auth/logout`
- `/api/v2/auth/guest-codes`

## Permission-Driven UI

- Guests see the same first-level product pages in the sidebar as administrators, including 实盘、盯盘、监控、数据 and AI研发.
- Guests can read page data outside Settings configuration, but backend auth rejects `/api/v2/settings/*`, live trading operations, position close, strategy pause/start/stop, sync writes, signal writes, AI mutations, and trading order/transfer/cancel calls.
- Guest sessions show a sticky top notice inside the main shell: `访客模式：部分页面功能不可用，仅支持查看和受限回测；策略启停、实盘控制、配置修改、数据/AI 写入需管理员权限。`
- Administrators see the full sidebar and can open Settings.
- Settings includes `访客邀请码管理` for administrators only. Its code-generation form pairs each field label with its input in the same responsive grid cell so labels remain visually aligned on desktop and mobile. The manager list shows active codes only; deleting a row revokes that code and removes it from the visible list while preserving backend audit history.

## Empty And Error States

- Loading: full-screen `正在检查登录态…`.
- Missing or expired session: login page remains visible and shows a clear expired/revoked message when `/auth/me` reports unauthenticated after a previous session.
- Invalid credentials or guest code: inline error; no main layout is rendered.

## Screenshot Contract

README screenshots should not be refreshed from a mocked login state. If login screenshots are added later, capture them from a real running app and document the session/auth state, source URL, viewport, and data state.
