# chat-ui — Shoppin Quarterly

Editorial Next.js frontend for the D2C AI Employee. Two pages:

- `/` — Conversation view. Tufte-style margin citations align to each cited
  number in the answer prose. Mobile collapses sidenotes inline.
- `/runs` — Agent Bench. Reads `core.agent_runs` and renders each as a
  research note (HIGH/MED/LOW band, reasoning quote, proposed action, top
  features with dot-leader, cited evidence).

## Prerequisites

- Backend running on `http://localhost:8000`. From the repo root:

  ```bash
  docker compose up -d postgres redis
  uv run alembic upgrade head
  PYTHONPATH=. uv run python scripts/seed_demo_tenant.py
  uv run uvicorn packages.api.main:app --port 8000
  ```

  CORS is configured for `http://localhost:3000` in `packages/api/main.py`.

## Dev

```bash
npm install
npm run dev          # http://localhost:3000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

## Build

```bash
npm run build
npm run start
```

## Aesthetic

- Fonts: **Fraunces** (display + numerals + body editorial), **Geist Sans**
  (UI), **Geist Mono** (citations, IDs).
- Palette in OKLCH (see `app/globals.css`). Cream paper, warm brown ink,
  terracotta accent used sparingly. Dark mode follows `prefers-color-scheme`.
- No rounded cards, no shadows, no gradients, no chat bubbles. Hairline
  rules separate sections.
