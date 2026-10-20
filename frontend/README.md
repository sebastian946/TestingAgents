# frontend — the web dashboard

React 19 + Vite + TypeScript + Tailwind 4. A single-page app that talks to the API with
the bearer token from `POST /auth/login`: sign up, log in, create jobs, watch them run,
read the scenarios and download the Excel report.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, with the API on http://127.0.0.1:8000
npm run build      # production bundle in dist/
npm run lint       # oxlint
```

The design system (tokens, components, the landing) is the "Tests Wise" Design System artifact;
`src/index.css` and `public/landing.css` are its implementation. Google Fonts are loaded from
`index.html` and the landing (the CSP in `app/deploy/Caddyfile` allows them).

In development Vite proxies `/auth`, `/jobs`, `/billing/` and `/Health` to the API (`vite.config.ts`),
so the browser sees one origin and no CORS setup is needed. Because those prefixes are forwarded, SPA routes
never start with `/jobs`, `/auth`, `/Health` or `/docs` (they are `/dashboard`, `/new`, `/job/:id`, ...). In production `/` is the static landing, so the dashboard lives at `/dashboard`; in development `/` forwards there. In production the `Dockerfile`
builds the bundle into a Caddy image that serves it and proxies the same paths
(`app/deploy/Caddyfile`); `docker-compose.prod.yml` uses that image.

Structure:

| File | Role |
|---|---|
| `src/api.ts` | fetch wrapper (token header, error parsing), the API types, the report download |
| `src/auth.tsx` | session state (`AuthProvider`, `useAuth`, `RequireAuth`); the token lives in `localStorage` |
| `src/index.css` | the Tests Wise tokens (CSS variables, light + dark via `prefers-color-scheme`, mirrored into Tailwind with `@theme inline`) and every shared component class: buttons, inputs, cards, badges, notices, header, tables, scenario accordion, plan cards, dialog |
| `src/pages/` | `LoginPage`, `RegisterPage` (one shared `AuthForm`), `VerifyEmailPage`, `ForgotPasswordPage`, `ResetPasswordPage`, `JobsPage` (`/dashboard`), `NewJobPage`, `JobDetailPage`, `BillingPage`, `AccountPage` |
| `src/components/` | `Layout` (header with mobile menu, verify banner, footer), `Logo` (inline SVG mark + wordmark), `ConfirmDialog` (replaces `window.confirm`), `ui.tsx` (`StatusBadge`, `PriorityBadge`, `Notice`, `ErrorBox`, formatting helpers) |
| `public/landing.html` + `landing.css` + `landing.js` | the public landing, bilingual (every string has `data-en` / `data-es`; the switch remembers the choice); served by Caddy at `/` |
| `public/terms.html`, `public/privacy.html` | legal pages (drafts: have them reviewed), served at `/terms` and `/privacy` |
| `public/sample-report.xlsx` | a real report (saucedemo.com) linked from the landing |

The jobs list and the job page poll the API every few seconds while a job is `queued` or
`running`, and stop when it finishes (ADR-03). The report is fetched with the token and
handed to the browser as a blob, because a plain link cannot carry the header.
