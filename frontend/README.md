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

In development Vite proxies `/auth`, `/jobs` and `/Health` to the API (`vite.config.ts`),
so the browser sees one origin and no CORS setup is needed. Because those prefixes are forwarded, SPA routes
never start with `/jobs`, `/auth`, `/Health` or `/docs` (they are `/new`, `/job/:id`, ...). In production the `Dockerfile`
builds the bundle into a Caddy image that serves it and proxies the same paths
(`app/deploy/Caddyfile`); `docker-compose.prod.yml` uses that image.

Structure:

| File | Role |
|---|---|
| `src/api.ts` | fetch wrapper (token header, error parsing), the API types, the report download |
| `src/auth.tsx` | session state (`AuthProvider`, `useAuth`, `RequireAuth`); the token lives in `localStorage` |
| `src/pages/` | `LoginPage`, `RegisterPage` (one shared `AuthForm`), `JobsPage`, `NewJobPage`, `JobDetailPage` |
| `src/components/` | `Layout` (header, log out), `ui.tsx` (badges, error box, formatting helpers) |

The jobs list and the job page poll the API every few seconds while a job is `queued` or
`running`, and stop when it finishes (ADR-03). The report is fetched with the token and
handed to the browser as a blob, because a plain link cannot carry the header.
