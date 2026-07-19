# NoLoop

> **Expanded Developer Guide**
>
> This version preserves the original README while adding additional context,
> explanations, architecture notes, troubleshooting guidance, and best
> practices. The original content begins below and remains unchanged.

---

# Additional Documentation

## Project Overview

NoLoop is an AI-powered health insurance claims platform designed to reduce
claim adjudication time from hours to seconds. The system is split into three
independent services:

- **Web Frontend** – User interface for hospitals, insurers, patients, and administrators.
- **API Backend** – Business logic, authentication, claim processing, and database access.
- **AI Engine** – Performs claim adjudication and generates explanations.

### Typical Request Flow

1. User signs in through the frontend.
2. Frontend calls the backend API.
3. Backend validates authentication.
4. Backend reads/writes PostgreSQL.
5. Backend sends claims to the AI engine.
6. AI engine returns adjudication results.
7. Backend stores results and returns them to the frontend.

## Repository Structure

Each top-level directory has a distinct responsibility:

- `app/` – Next.js routing.
- `components/` – Shared React components.
- `lib/` – API client, authentication helpers, utilities.
- `backend-py/` – FastAPI backend.
- `ai/` – AI adjudication service.
- `scripts/` – Database initialization and demo data.
- `data/` – Synthetic datasets and generated samples.

## Development Workflow

1. Clone the repository.
2. Install prerequisites.
3. Configure PostgreSQL.
4. Configure environment variables.
5. Start AI Engine.
6. Start Backend.
7. Initialize database.
8. Seed demo data.
9. Start Frontend.
10. Log in using demo credentials.

For every step below, the original README has been preserved exactly.

---

# Original README (Unmodified)

# NoLoop

**Cashless health-insurance claims in ~60 seconds.**

NoLoop connects hospitals and insurers on a single AI-powered platform, cutting
claim turnaround from hours to seconds. Hospitals submit claims, an AI engine
adjudicates them, and insurers review the results — all in one place.

---

## 1. What's in this folder

This directory bundles the three services that make up the app:

| Folder        | Service                     | Stack                                   | Port   |
| ------------- | --------------------------- | --------------------------------------- | ------ |
| `app/`, `components/`, `lib/` | **Web frontend** | Next.js 15 · React 19 · Tailwind v4     | `3000` |
| `backend-py/` | **API backend**             | Python · FastAPI · SQLAlchemy (asyncpg) | `4000` |
| `ai/`         | **AI adjudication engine**  | Python · FastAPI · Groq (optional)      | `8000` |

**How they connect:**

```
  Browser ──▶ Web frontend (:3000) ──▶ API backend (:4000) ──▶ Postgres
                                              │
                                              └──▶ AI engine (:8000)
```

- The **web frontend** calls the backend at `NEXT_PUBLIC_API_URL` (default `http://localhost:4000`).
- The **backend** stores everything in **Postgres** and calls the **AI engine** to adjudicate claims (falling back to an in-process rule engine if the AI engine is down).
- The **AI engine** is stateless — it needs no database.

The web app has role-based portals — **hospital** (`/hospital`), **insurer**
(`/insurer`), and **patient** (`/patient`) — plus a public landing page. After
login, users are routed to their portal by role (see `lib/api.ts`).

---

## 2. Prerequisites

Install these first (the commands below assume **Windows + PowerShell**, the
default terminal in VS Code / Windows Terminal):

| Tool         | Version | Check              | Get it                          |
| ------------ | ------- | ------------------ | ------------------------------- |
| **Python**   | 3.11+   | `python --version` | <https://www.python.org/downloads/> |
| **Bun**      | latest  | `bun --version`    | <https://bun.sh>                |
| **Postgres** | 14+     | `psql --version`   | <https://www.postgresql.org/download/windows/> (or use Supabase — see §3) |

> **Note on `psql`:** the Windows Postgres installer does **not** add `psql` to
> your PATH by default, so `psql --version` may say "not recognized" even though
> Postgres is installed and running. You can either use **SQL Shell (psql)** from
> the Start menu, or call the full path, e.g. for Postgres 18:
> `& "C:\Program Files\PostgreSQL\18\bin\psql.exe"` (adjust `18` to your version).

> After installing Python, **reopen your terminal** so `python` is on the PATH.
> During the Python installer, tick **"Add python.exe to PATH."**

One-time PowerShell step so it can run the `.ps1` start scripts:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## 3. Set up Postgres (do this before the backend)

The backend needs a Postgres database. Pick **one** of the two options below.

> ⚠️ **Important:** this repo has **no auto-migrations**. A fresh database is
> empty, so after creating it you must run **`scripts/init_db.py`** once (§3.3)
> to create the tables and enum types. The backend will connect to an empty DB
> but every real request will fail until the schema exists.

### Option A — Local Postgres (recommended for development)

1. Install Postgres (link in §2). During setup you'll set a password for the
   `postgres` superuser — remember it.

2. Create the database. Open **SQL Shell (psql)** from the Start menu (press
   Enter through the prompts, type your password), then run:

   ```sql
   CREATE DATABASE noloop;
   ```

   *(Optional — a dedicated user instead of `postgres`:)*

   ```sql
   CREATE USER noloop WITH PASSWORD 'noloop';
   GRANT ALL PRIVILEGES ON DATABASE noloop TO noloop;
   ```

3. Your connection string (used in `.env` next section) is:

   ```
   DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/noloop
   ```

### Option B — Supabase (hosted Postgres, no local install)

1. Create a free project at <https://supabase.com>.
2. In the dashboard: **Project Settings → Database → Connection string → URI**.
3. Use the **pooled** connection (port `6543`) for `DATABASE_URL`:

   ```
   DATABASE_URL=postgresql://postgres.xxxx:YOUR_PASSWORD@aws-0-region.pooler.supabase.com:6543/postgres?pgbouncer=true&sslmode=require
   ```

   The backend automatically handles the `pgbouncer` / `sslmode` params.

### 3.3 Create the schema (both options)

After the database exists **and** your backend `.env` is filled in (§4, Step 2),
create the tables once from inside `backend-py`:

```powershell
cd backend-py
.\.venv\Scripts\python.exe scripts\init_db.py
```

This creates all enum types and tables. It's safe to re-run. *(You'll have a
`.venv` after running `./start.ps1` once — or create it first with the Step 2
commands below.)*

---

## 4. Run the app

The app is **three services**, each in **its own terminal**, started in this
order. Keep all three terminals open while using the app.

| Order | Service     | Folder        | Opens at                | Needs        |
| ----- | ----------- | ------------- | ----------------------- | ------------ |
| 1st   | AI engine   | `ai/`         | http://localhost:8000   | —            |
| 2nd   | API backend | `backend-py/` | http://localhost:4000   | AI engine + Postgres |
| 3rd   | Web frontend| `.` (here)    | http://localhost:3000   | API backend  |

> Instructions are **Windows-first (PowerShell)**. macOS / Linux / Git Bash
> equivalents follow each block under _"On macOS / Linux."_

### Step 1 — AI engine (`:8000`)

**Terminal 1:**

```powershell
cd ai
cp .env.example .env      # creates config; a Groq key is optional
./start.ps1               # first run builds .venv + installs deps, then starts
```

Leave it running. **Check:** open <http://localhost:8000/docs>.

> The engine works **without** a Groq key (deterministic rationales). A free key
> from <https://console.groq.com/keys> just makes rationales read more naturally
> — paste it into `ai/.env` as `GROQ_API_KEY=...`.

**On macOS / Linux:** `cd ai && ./start.sh`

### Step 2 — API backend (`:4000`)

**Terminal 2:**

```powershell
cd backend-py
cp .env.example .env      # then edit .env — see the table below
notepad .env              # fill in DATABASE_URL and JWT_SECRET, save & close
./start.ps1               # first run builds .venv + installs deps, then starts
```

`./start.ps1` creates the virtualenv, installs dependencies, and launches the
server — you do **not** need `python -m venv`, `source`, or `pip install` by
hand on Windows.

> ⚠️ **The backend will not start until you replace the placeholders in
> `backend-py/.env`.** Out of the box `DATABASE_URL` points at a fake Supabase
> host (`USER:PASSWORD@HOST`) and `JWT_SECRET` is `change-me-...`. You **must**
> edit both, or `./start.ps1` fails to connect and never binds port 4000.

**Fill these in `backend-py/.env`** (full list in `.env.example`):

| Variable        | What to put                                                        |
| --------------- | ------------------------------------------------------------------ |
| `DATABASE_URL`  | Your Postgres URL from §3 (required)                               |
| `DIRECT_URL`    | Same value as `DATABASE_URL` for local Postgres                    |
| `JWT_SECRET`    | Any long random string (keep it secret)                            |
| `AI_ENGINE_URL` | Leave as `http://localhost:8000` (the Step 1 service)             |
| `API_PORT`      | Leave as `4000` (the frontend expects it)                          |

For **local Postgres** (§3 Option A), the lines should look like this — swap in
the password you set during install and the database name you created:

```dotenv
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/noloop
DIRECT_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/noloop
JWT_SECRET=any-long-random-string-you-make-up
```

**First-time database setup** — after the server starts once and a `.venv`
exists, stop it (`Ctrl+C`) and run these once to create the schema and demo data:

```powershell
.\.venv\Scripts\python.exe scripts\init_db.py                              # create tables
.\.venv\Scripts\python.exe scripts\seed_demo.py                            # seed demo orgs + claims
.\.venv\Scripts\python.exe scripts\create_platform_admin.py admin@noloop.in "StrongPass123"
```

Then start the server again with `./start.ps1`.

**Check:** open <http://localhost:4000/health> → `{"status":"ok","db":"connected"}`.
API docs: <http://localhost:4000/docs>.

**On macOS / Linux / Git Bash:**

```bash
cd backend-py
python -m venv .venv
source .venv/bin/activate          # Git Bash on Windows: source .venv/Scripts/activate
pip install -e .
cp .env.example .env               # then edit .env (table above)
python scripts/init_db.py          # first-time: create schema
python scripts/seed_demo.py        # first-time: seed demo data
./start.sh                         # uvicorn on :4000
```

### Step 3 — Web frontend (`:3000`)

**Terminal 3** (in this folder, `noloop-app/`):

```powershell
bun install       # first run only
bun run dev       # starts the Next.js dev server
```

(These `bun` commands are identical on Windows, macOS, and Linux.)

**Check:** open <http://localhost:3000>. To point the frontend at a different
backend, create `.env.local` here:

```bash
# .env.local
NEXT_PUBLIC_API_URL=http://localhost:4000
```

---

## 5. Demo logins

After running `scripts/seed_demo.py`, log in at <http://localhost:3000/login>:

| Role            | Email                                       | Password         |
| --------------- | ------------------------------------------- | ---------------- |
| Hospital admin  | `meadowpine.hospital@noloop.in`             | `Hospital@123`   |
| Hospital staff  | `nurse.meadowpinehospital@noloop.in`        | `Staff@123`      |
| Insurer admin   | `everwell.assurance@noloop.in`              | `Insurer@123`    |
| Adjudicator     | `adjudicator.everwellassurance@noloop.in`   | `Adjudicator@123`|
| Platform admin  | whatever you passed to `create_platform_admin.py` | (your choice) |

---

## 6. Backend scripts

Run from `backend-py/` (prefix with `.\.venv\Scripts\python.exe` on Windows, or
activate the venv first):

| Script                          | What it does                                                     |
| ------------------------------- | --------------------------------------------------------------- |
| `scripts/init_db.py`            | Create enum types + tables on a fresh database (run once)        |
| `scripts/seed_demo.py`          | Seed a demo hospital, insurer, policy, patients, beds, and ~52 AI-adjudicated claims (idempotent) |
| `scripts/create_platform_admin.py <email> <password> [name]` | Create/update a platform-admin login |
| `scripts/generate_synthetic_claims.py [count] [seed]` | Write reproducible synthetic claim packets to `data/synthetic/` |

See `backend-py/README.md` for architecture notes.

---

## 7. Troubleshooting

| Symptom | Fix |
| ------- | --- |
| Backend exits immediately / nothing listening on `:4000` | You didn't edit `backend-py/.env` — replace the placeholder `DATABASE_URL` (and `JWT_SECRET`) as shown in §4 Step 2. |
| `psql : not recognized` (PowerShell) | Installer didn't add it to PATH — use **SQL Shell (psql)** from the Start menu, or the full path `C:\Program Files\PostgreSQL\<version>\bin\psql.exe`. |
| `python : not found` (PowerShell) | Install Python 3.11+, tick "Add to PATH", reopen the terminal. |
| `source : not recognized` (PowerShell) | `source` is bash-only. Use `./start.ps1`, or activate with `.\.venv\Scripts\Activate.ps1`. |
| `./start.ps1 cannot be loaded ... execution policy` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry. |
| Health check shows `db` not `connected` | `DATABASE_URL` is wrong or Postgres isn't running / reachable. |
| Health OK but requests 500 / "relation does not exist" | Schema not created — run `python scripts\init_db.py` (§3.3). |
| Want a clean Python reinstall | `Remove-Item -Recurse -Force .venv; ./start.ps1` |
| Port already in use | Another process holds `:4000` / `:8000` / `:3000` — stop it, or change `API_PORT` in `backend-py/.env`. |

---

## 8. Production build (web)

```powershell
bun run build
bun run start
```

## Scripts (web)

| Command         | Description                          |
| --------------- | ------------------------------------ |
| `bun run dev`   | Start the Next.js dev server (:3000) |
| `bun run build` | Build the production bundle          |
| `bun run start` | Serve the production build           |
