# Prompts for Claude Code

Paste one at a time. Don't start the next until the current one's
"Done when" is fully met and you've seen the test output yourself.
Start each in a fresh Claude Code session if the previous one got long.

---

## 0 · Orientation (no code changes)

```
Read CLAUDE.md and docs/candidate-brief.pdf fully. Then read every file in
backend/app/core and backend/tests. Run `cd backend && python -m pytest -q`
and show me the output.

Do not change any code. Reply with:
1. A short summary of the architecture in your own words.
2. Anything in CLAUDE.md you think is wrong, risky, or missing, with reasons.
3. Your plan for Phase 1.
```

Read its answer. If it proposes weakening a rule in CLAUDE.md, push back.

---

## 1 · Evaluate + export (pure logic)

```
Phase 1. Build backend/app/core/evaluate.py and backend/app/core/export.py
exactly as CLAUDE.md describes. Pure functions, no database.

evaluate(lines, decisions) -> effective lines + summary. Each effective line
has: line_id, source_row, effective values, original parsed values, cells,
issues, status (included | excluded), status_reason, decision (if any),
needs_decision_open (needs_decision issue and no decision yet), line value in
units. Summary: included line count, pieces, supplier cost units, retail
reference units (only lines with a usable positive retail), counts per
category (needs decision open, warnings, fixed, auto excluded, user excluded).

Also add validate_change(line, change) returning a list of plain-English
errors, implementing the include rules in CLAUDE.md. It must accept only
integer quantity (reject 12.5, "12", true) and decimal-string cost.

Write backend/tests/test_evaluate.py covering: default totals equal the
answer key for all three files; including R14 at $5.00 gives 1,833 pieces
and 4708.00; excluding a clean line removes it; reset restores the default;
overriding A104's cost to 9.00 and including it adds 60 pieces and 540.00;
invalid include changes are rejected with clear messages; export sums equal
the summary for all three files; formula-injection text is escaped.

Done when: all tests pass (paste output), test_core.py untouched and green.
```

---

## 2 · Storage + API

```
Phase 2. Explain the plan first (tables, transaction boundaries, how
idempotency and versioning work together), wait for my OK, then build.

Build app/db.py, app/models.py, app/service.py, app/api.py, app/main.py per
CLAUDE.md. DATABASE_URL env var; default to a local SQLite file. Create
tables on startup (no migration tool needed for this scope). Store lines with
a bulk insert, not one ORM add per row.

Implement every endpoint in the API contract, including Idempotency-Key on
upload, request_id + base_version on decisions, 409 on stale version, 422
atomic validation, and fault injection behind FAULT_INJECTION=1.

Add Server-Timing headers on upload, get, decisions and export with stages
parse, analyze, db, evaluate, serialize.

Write backend/tests/test_api.py using FastAPI TestClient against a temporary
SQLite database:
- upload each fixture; GET summary equals the answer key
- same Idempotency-Key twice -> one offer
- two uploads without a key -> two separate offers, independent decisions
- save decision -> version 2; same request_id again -> identical response,
  still version 2, decision row count unchanged
- after-commit fault then retry with same request_id -> applied exactly once
- before-commit fault -> nothing saved, version unchanged
- stale base_version -> 409, nothing saved
- invalid quantity (-5, 0, 12.5, "12") and invalid cost ("abc", "-1",
  "1.23456", "0") posted directly -> 422, offer JSON before == after
- one invalid change in a batch of three -> none applied
- export.csv totals == GET summary totals, for all three fixtures
- unknown offer id -> 404; unsupported file -> 422 with a clear message

Done when: all tests pass (paste output), including test_core.py.
```

---

## 3 · Frontend

```
Phase 3. First propose the screen layout and component structure, wait for
my OK, then build.

Create frontend/ with Vite + React + TypeScript. FastAPI serves the built
files and falls back to index.html for /offers/{id}. Follow "Frontend
behaviour" in CLAUDE.md exactly. The frontend never computes money; it shows
the strings the API returns.

Must have:
- Upload page with recent offers.
- Offer page: summary with supplier cost total clearly separate from the
  retail reference; tabs Needs decision / Warnings / Auto-fixed / Excluded /
  All lines, each with a count; notices panel.
- Line detail: original cells (coordinate + raw) beside cleaned values,
  issues, include (edit code/pieces/cost) / exclude / reset. Conflict groups
  shown together with "keep this one".
- Staged edits + save bar with all states in CLAUDE.md. Retry must reuse
  the same request_id. On 409, offer to reload and re-apply.
- Export disabled with unsaved changes.
- All-lines tab virtualized (@tanstack/react-virtual) so 5,000 rows scroll smoothly.
- Plain, clean, readable styling. No UI framework needed.

Then run the app locally, upload all three fixtures, resolve A108 by keeping
row 14, fix A104's cost to 9.00, save, reload in a new private window, and
confirm the decisions persisted and export matches the screen. Report what
you checked and anything that didn't work.

Done when: npm run build succeeds, backend tests still pass, and the manual
check above is reported.
```

---

## 4 · Evidence

```
Phase 4. Produce the evidence section of the brief.

1. scripts/measure.py: given a base URL, uploads the 5,000-row fixture,
   fetches the offer, saves one decision, downloads the export. Run it 3
   times (first + repeats). Record wall-clock time per step and the
   Server-Timing stages. Output a markdown table. Identify the largest delay.
   If you optimize anything, show before/after tables with identical totals.
2. docs/failure-demo.md: exact steps to reproduce the after-commit failure
   (FAULT_INJECTION=1 + a UI toggle or curl), what the user sees before and
   after retry, and the decisions/version in the database before and after.
   Include the commands and their real output.
3. docs/data-protection.md: curl commands posting invalid quantity and price
   straight to the API, the 422 responses, and proof the offer is unchanged
   (GET before/after, compare).

Use real output only. Never invent numbers.

Done when: all three documents exist with real output, and tests still pass.
```

---

## 5 · Deploy + handoff

```
Phase 5. Add a Dockerfile (multi-stage: build frontend, then Python image
serving everything with uvicorn) and render.yaml. Document setup in README.md:
local run, tests, env vars (DATABASE_URL, FAULT_INJECTION), and deploying to
Render with a Neon Postgres database.

Make sure the app works against Postgres, not just SQLite: run the API test
suite against a Postgres DATABASE_URL if one is available and report the result.

Then write HANDOFF.md per CLAUDE.md: workflow, architecture diagram,
main decisions and why, storage, assumptions, evidence (link the docs from
Phase 4 with their real numbers), known gaps, ongoing hosting cost, and AI
usage (what was generated, what I reviewed and verified by hand).

Done when: docker build succeeds and README steps work from a clean clone.
```

---

## After deploying (do these yourself)

1. Run `scripts/measure.py` against the live URL and paste the real table into HANDOFF.md.
2. Open a saved offer link in a different browser to confirm it persists.
3. Upload each fixture once on the live app and check the totals against the answer key.
4. Push to a public GitHub repo, note the commit hash in HANDOFF.md, and send Travis the app URL, a saved-offer URL, and the repo link.
