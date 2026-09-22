# Reflex supplier offer tool

Work sample for Reflex Sales Group. Upload a supplier line sheet, review what it
means, resolve questionable rows, save an offer at a shareable link, and export
a clean CSV.

Status: parsing engine, evaluation, storage, API and frontend complete and
tested. Evidence and deploy are next. See `CLAUDE.md` for the full design and
`PROMPTS.md` for the remaining build phases.

## Run it

```bash
cd frontend && npm install && npm run build   # builds into backend/static/
cd ../backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q                            # 141 passed, 6 skipped
uvicorn app.main:app --reload                  # http://localhost:8000
```

For frontend work, `cd frontend && npm run dev` runs Vite on :5173 and
proxies `/api` to :8000.

`python` must be 3.14. Without a `DATABASE_URL` the app creates
`backend/reflex_offers.db` (SQLite) in the working directory.

If your shell exports a `DATABASE_URL` for some other project, unset it for
this one — `env -u DATABASE_URL uvicorn app.main:app`. A URL the app can't use
(a `jdbc:` one, say) stops startup with an explanation rather than a
traceback.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./reflex_offers.db` | `postgresql+psycopg://...` in production |
| `FAULT_INJECTION` | unset | `1` enables the `X-Fault` header used by the failure demo |
| `TEST_DATABASE_URL` | unset | Run the test suite against Postgres instead of SQLite |

## Tests

```bash
python -m pytest -q                                   # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pass@host/reflex_test python -m pytest -q
```

The threaded concurrency tests skip on SQLite, which serialises writers, so the
version race they exercise cannot happen there. The database name must contain
`test` — the fixtures drop every table and refuse to run otherwise.

## Trying it by hand

```bash
BASE=http://localhost:8000

# upload and read back
ID=$(curl -s -F file=@backend/tests/fixtures/01-northstar-line-sheet.xlsx \
     $BASE/api/offers | python -c 'import json,sys;print(json.load(sys.stdin)["id"])')
curl -s $BASE/api/offers/$ID | python -m json.tool | head -40

# keep row 14 of the A108 conflict and close row 15, in one save
curl -s -X POST -H 'Content-Type: application/json' -d "{
  \"request_id\": \"$(uuidgen)\", \"base_version\": 1, \"changes\": [
    {\"line_id\": \"R14\", \"action\": \"include\", \"unit_cost\": \"5.00\"},
    {\"line_id\": \"R15\", \"action\": \"exclude\"}]}" \
  $BASE/api/offers/$ID/decisions

curl -s $BASE/api/offers/$ID/export.csv
```

`Server-Timing` on upload, read, save and export reports `parse`, `analyze`,
`db`, `evaluate` and `serialize` in milliseconds, which is what Phase 4 uses to
locate the slowest stage.
