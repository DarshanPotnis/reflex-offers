# Supplier offer tool

Suppliers send surplus-stock spreadsheets — "line sheets" — that each look a
little different, and someone has to clean up the codes, sizes, quantities and
prices before the offer can be used. This tool reads both supplied layouts,
shows exactly what it changed and what it could not decide, lets an operations
person fix or exclude the questionable rows, and saves the result at a
shareable link with a clean export that always matches the screen.

**Live app:** `<to be filled in after deploy>`
**Example saved offer:** `<to be filled in after deploy>`

## What it does

- **Reads both layouts.** Northstar (one row per item and size) and Harbor
  (sizes across columns), detected by column *name*, so reordered columns and
  title/footer rows are fine.
- **Never guesses.** `"$3.25"` and `" 45 "` are cleaned and the change is
  shown. A missing price, a negative quantity or `"TBD"` is left out and
  flagged, because fixing it would change what the number *means*.
- **Explains every omission in the list itself** — "No supplier cost",
  "Quantity is -12", "Duplicate of row 6" — without expanding anything.
- **Catches double-counting.** Identical rows are counted once by default;
  rows that disagree are all held back until a person chooses.
- **Keeps the money exact.** Every amount is an integer number of
  ten-thousandths of a dollar. No float touches a price, anywhere.
- **Survives a failed save.** A save that commits but fails to reach the
  browser can be retried without doubling anything, even after a page refresh.
- **Exports what you see.** `.xlsx` (item code `000101` stays `000101`) and
  `.csv`, both built from the same saved state as the screen.

Design and decisions: [docs/DESIGN.md](docs/DESIGN.md) ·
Handoff: [HANDOFF.md](HANDOFF.md) ·
Evidence: [speed](docs/speed.md), [failure and recovery](docs/failure-demo.md),
[data protection](docs/data-protection.md)

---

## Run it locally

Requires Python 3.14 and Node 24.

```bash
git clone <repo-url> && cd reflex-offers

cd frontend && npm install && npm run build   # builds into backend/static/
cd ../backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload                 # http://localhost:8000
```

Without a `DATABASE_URL` the app creates `backend/reflex_offers.db` (SQLite).

For frontend work, `cd frontend && npm run dev` runs Vite on :5173 and proxies
`/api` to :8000.

### With Docker

```bash
docker build -t reflex-offers .
docker run -p 8000:8000 -e DATABASE_URL="sqlite:////tmp/app.db" reflex-offers
```

## Tests

```bash
cd backend
python -m pytest -q                    # 177 passed, 6 skipped
```

Against Postgres, where the concurrency tests actually mean something:

```bash
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST/reflex_offers_test' \
  python -m pytest -q                  # 183 passed
```

The six skips are threaded race tests. SQLite serialises writers, so the
version race they exercise cannot happen there. **The database name must
contain `test`** — the fixtures drop every table and refuse to run otherwise.

The frontend is checked by driving a real browser:

```bash
cd frontend
npm install --no-save playwright && npx playwright install chromium
node uicheck.mjs http://localhost:8000 ../docs/screenshots
```

It needs the server started with `FAULT_INJECTION=1`. Its expectations are
read from the API, so it cannot pass by agreeing with itself.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./reflex_offers.db` | `postgresql+psycopg://…` in production |
| `FAULT_INJECTION` | unset | `1` enables the `X-Fault` header and the UI test toggle |
| `PORT` | `8000` | Set by Render |
| `TEST_DATABASE_URL` | unset | Runs the test suite against Postgres |
| `WEB_CONCURRENCY` | `1` (Render: `2`) | uvicorn worker processes |

If your shell exports a `DATABASE_URL` for another project, unset it for this
one — `env -u DATABASE_URL uvicorn app.main:app`. A URL the app cannot use (a
`jdbc:` one, say) stops startup with an explanation rather than a traceback.

---

## Deploying to Render + Neon

One web service and one database, both in **Ohio**. Co-locating them matters:
measured from a laptop in Los Angeles the offer reads took seconds, almost
entirely transfer time ([docs/speed.md](docs/speed.md)).

### 1. Create the Neon database

1. Sign in at [neon.tech](https://neon.tech) and create a project in
   **AWS US East (Ohio)**.
2. Keep the default database, **`neondb`**.
3. Copy the connection string from **Connection Details**. It looks like
   `postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require`.
4. **Change the scheme to `postgresql+psycopg://`** — SQLAlchemy needs the
   driver named. Keep `?sslmode=require`.

Optionally create a second database named **`reflex_offers_test`** on the same
project for running the test suite against Postgres. Do not point the tests at
`neondb`: the fixtures drop every table.

### 2. Create the Render service

1. Push this repository to GitHub.
2. In Render, **New → Blueprint**, and select the repo. It reads
   [`render.yaml`](render.yaml): one Docker web service, region Ohio, health
   check on `/api/health`.
3. Render prompts for **`DATABASE_URL`**, because it is marked `sync: false`
   and is never committed. Paste the `postgresql+psycopg://…/neondb` string.
   Everything else — plan, region, workers — comes from the blueprint. The
   service is Blueprint-managed, so **changing the plan in the dashboard is
   reverted on the next deploy**; edit `render.yaml` instead.
4. Deploy. The first build takes a few minutes (it builds the frontend, then
   the Python image). Tables are created on startup — there is no migration
   step at this scale.

`FAULT_INJECTION=1` is set in `render.yaml` so the failure demo can be
reproduced in the live app. It only enables the `X-Fault` header and the
labelled UI toggle; nothing fails on its own. Remove it for real use.

### 3. Check it

```bash
curl https://<your-app>.onrender.com/api/health
python scripts/measure.py https://<your-app>.onrender.com --runs 3
```

`measure.py` re-checks the answer key on every run and refuses to report
timings for an offer whose totals have drifted.

### Cost

`render.yaml` specifies **Starter ($7/month)** for the review window. Free is
$0 but **sleeps after 15 minutes idle** — the first request then waits
~30–60 s — and its ~0.1 vCPU made CPU the bottleneck in every measurement
([docs/speed.md](docs/speed.md)). Neon's free tier is $0 and suspends when
idle. Set `plan: free` in `render.yaml` when the review is over.
