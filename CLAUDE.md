# CLAUDE.md — Reflex supplier offer tool

Read this file fully before doing anything. It is the source of truth for
this project. If a request conflicts with it, stop and ask.

## What we are building

A work sample for Reflex Sales Group (brief: `docs/candidate-brief.pdf`).
Suppliers email messy spreadsheets ("line sheets"). An operations person
uploads one, reviews what it means, fixes or excludes questionable rows,
saves an **offer** at a shareable link, and downloads a clean CSV.

Reviewers will try it themselves, change values in the files, reorder
columns, add rows, force a failed save, and send invalid data straight to
the API. They care most about business judgment, reliability, and whether
we can explain every decision. Visual polish is secondary. Time budget: one day.

## Core principle (do not weaken)

Every issue is sorted by one question: **does fixing it change what the
number means, or only how it was written?**

| Kind | Meaning | Default |
| --- | --- | --- |
| `fixed` | Form changed, meaning certain (`"$3.25"`, `" 45 "`, `"1,200"`, leading zeros) | Included, change shown, reversible |
| `warning` | Usable but worth a look (cost > retail, supplier total mismatch, kept duplicate, duplicate created by an edit) | Included, flagged |
| `auto_excluded` | Unambiguously not stock (0 pieces) | Excluded, reason shown |
| `needs_decision` | Could change a quantity or price (missing/invalid/negative, duplicate copy, conflicting rows, missing code) | Excluded until a person decides |

Policy: **never overstate what is available, never guess a price, never
invent an identifier.** An offer is always safe to save with open questions.

### Duplicates vs conflicts

Grouping is on (`item_code`, `size`), case-insensitive. Applying the core
principle to a group:

- **Duplicate** — members agree on `quantity`, `unit_cost` and `retail`. The
  numbers are not in dispute, so a differing `description` or `category` is
  text, not meaning. The first row by source row is kept (`DUPLICATE_KEPT`,
  warning); every copy is `DUPLICATE_ROW` (needs_decision) so stock is never
  counted twice. Where the text differs, the message names the difference.
- **Conflict** — members disagree on `quantity`, `unit_cost` or `retail`.
  `CONFLICTING_ROWS` (needs_decision) on every member; all are excluded until
  a person picks one.

Only quantity, cost and retail decide which it is. Description and category
never promote a duplicate to a conflict.

## Current state (verified — do not regress)

`backend/` and `frontend/` are both done. Backend: `test_core.py`,
`test_evaluate.py`, `test_api.py` — 141 passing, 6 skipped (the skips are the
threaded race tests, meaningful only on Postgres; 147 pass on Neon). Run
`cd backend && python -m pytest -q` before and after every change.

The frontend is checked by driving a real browser:
`cd frontend && node uicheck.mjs http://localhost:PORT` against a server
started with `FAULT_INJECTION=1` — 39 checks covering upload, the conflict
group, save, the fault toggle, a refresh mid-failure, retry, 422, the
two-window 409, persistence in a fresh session, export, and virtualization.
Its expectations are read from the API so it cannot pass by agreeing with
itself. It needs `npm i --no-save playwright && npx playwright install
chromium` (deliberately not a saved dependency).

SQLAlchemy is pinned at **2.0.54**, not 2.0.36: the older pin cannot resolve
`Mapped[str | None]` on Python 3.14 and fails at import.

- `money.py` — all money is `int` ten-thousandths of a dollar ("units").
  $4.50 == 45_000. Never use float for money anywhere, including JSON and
  the frontend. Refuses to round (>4 decimal places is a data error).
- `normalize.py` — cell parsers. Return `Parsed(value, status, note)` with
  status `ok | normalized | missing | invalid`. They never guess.
- `reader.py` — detects layout by **column names** (case/space-insensitive),
  never positions. Skips and reports title/footer rows. Harbor: one line per
  non-blank size cell; supplier `Total Units` is only checked, never used.
  Line ids: Northstar `R{row}`, Harbor `R{row}-{SIZE}`. These ids are stable
  and are the key for decisions.
- `rules.py` — per-line issues + duplicate/conflict grouping on
  (item_code, size), case-insensitive. See "Duplicates vs conflicts" above.
- `evaluate.py` — `StoredLine` (what storage holds) + `store()` to adapt a
  fresh `Analysis`; `prepare_change`/`validate_change` (the validation gate,
  returns the `Decision` to store); `evaluate()` -> effective lines +
  summary, including the `DUPLICATE_AFTER_EDIT` re-check.
- `export.py` — `export_rows()` / `to_csv()` from evaluated lines only.
- `db.py`/`models.py`/`service.py`/`api.py`/`main.py` — storage and HTTP.
  Every transaction boundary is in `service.py`; `api.py` only translates.

### Answer key (default policy, no user decisions)

| File | Included lines | Pieces | Supplier cost | Retail reference |
| --- | --- | --- | --- | --- |
| 01 Northstar | 6 | 1,733 | 4208.00 | 21552.00 |
| 02 Harbor | 22 | 1,340 | 3740.00 | 16144.00 |
| 03 Northstar 5,000 | 5,000 | 62,444 | 187214.50 | 1031508.00 |

If the 01 row-14 A108 line is included at $5.00: 1,833 pieces, 4708.00.

Traps these encode (all in tests): duplicate 000101 rows 6/10 (count once),
leading zeros, `"$3.25"`, missing cost A104, qty -12, qty 0, A108 same item
two costs (conflict, both excluded), blank code (scarf), `"1,200"`, `"TBD"`,
`" 45 "`, Harbor M=`TBD`, all-zero row, missing cost row, M=-3 with supplier
total 21 vs usable 24. The 5,000-row file claims "every row is valid"; it is,
but reviewers may inject bad rows — the same rules must catch them.

## Architecture

```
backend/   FastAPI, SQLAlchemy 2, Postgres (prod) / SQLite (dev, tests)
  app/core/        pure logic, no I/O (done: money, normalize, reader, rules)
  app/core/evaluate.py   done: lines + decisions -> effective lines + summary
  app/core/export.py     done: CSV rows from evaluated lines
  app/db.py, app/models.py, app/service.py, app/api.py, app/main.py  (done)
    service.py is the only module that commits; api.py computes nothing
frontend/  React + TypeScript + Vite (done). `npm run build` emits straight
           into backend/static/, which FastAPI serves with an index.html
           fallback. uicheck.mjs drives it in a real browser.
```

One deployable service (Render web service + Neon Postgres). One URL.

**Single source of truth:** the server computes every total, validates every
edit, and builds the export from the *saved* state using the same
`evaluate()` function. The frontend never computes money; it displays what
the server returns. This is what guarantees "the download agrees with the screen".

`evaluate()` takes a storage-neutral `StoredLine` — exactly the fields
`offer_lines` holds, money already in int units — never a `SourceLine` or a
raw DB row. The upload path adapts `Analysis` into it once; every later GET
and export builds it from the database. Same function, same numbers, no
re-reading the workbook.

`evaluate()` also re-checks grouping on *effective* values: if a user's edits
leave two **included** lines sharing an (`item_code`, `size`), both get a
`DUPLICATE_AFTER_EDIT` warning. Upload-time grouping is frozen in
`offer_lines.issues`, so without this an override that collides with another
line, or including both halves of a conflict, would overstate stock silently.
Warning only, never a block — two real lots are possible.

## Data model

- `offers`: `id` (uuid4 string, the shareable link), `created_at`,
  `updated_at`, `version` (int, starts at 1), `supplier_name`, `title`,
  `layout`, `sheet_name`, `source_filename`, `source_sha256`, `notices` (json),
  `upload_key` (unique, nullable — idempotency for upload).
- `offer_sources`: `offer_id` pk, `bytes` (original file, for "check against original").
- `offer_lines`: pk (`offer_id`, `line_id`), `position`, `source_row`,
  parsed values (`item_code`, `description`, `size`, `category`, `quantity`,
  `unit_cost_units`, `retail_units`, plus each field's status/note),
  `cells` (json: field -> {coordinate, raw}), `issues` (json),
  `related_line_ids` (json), `default_included` (bool). Written once at upload.
- `decisions`: pk (`offer_id`, `line_id`), `action` (`include|exclude`),
  optional overrides `item_code`, `size`, `quantity`, `unit_cost_units`,
  `note`, `updated_at`. **Upserted**, never appended — saving the same
  decision twice cannot double anything.
- `save_requests`: pk (`offer_id`, `request_id`), `receipt` (json),
  `body_sha256`, `created_at`. Idempotency for saves. The receipt is minimal
  — `{offer_id, request_id, version, applied}` — never the full offer and not
  the summary either: the client re-fetches after every save, so totals and
  lines always arrive in the same response and cannot disagree. (The 5,000-row
  offer serialises to ~3.2 MB; Phase 4 measures that refetch and optimises it
  then, with a measurement rather than a guess.) `body_sha256` is over the
  **canonical JSON** of the parsed payload — sorted keys, normalised — not the
  raw bytes, so a client that re-serialises an identical retry isn't accused
  of changing it.

## API contract

All money in JSON is a **string** decimal (`"4.50"`). Pydantic models use
`extra="forbid"`.

- `POST /api/offers` — multipart `file`, optional header `Idempotency-Key`.
  Returns a **receipt**, `{offer_id, version}` — not the offer. The client
  navigates to the offer page and fetches it there, so everything on screen
  comes from one GET. Returning the full offer here meant reading all 5,000
  stored lines back to build a response the UI discarded a moment later.
  Same key again -> same offer's receipt (200), no second offer. Same key with a
  *different* file (`source_sha256` differs) -> 409; silently returning the
  first offer would hide the second upload entirely. Body over 10 MB -> 413.
  Unsupported file -> 422 with the reader's plain-English message.
- `GET /api/offers` — the 50 most recent offers (id, supplier, filename,
  created, version).
- `GET /api/offers/{id}` — offer meta, `version`, `notices`, evaluated
  `lines`, `summary`. 404 if unknown.
- `POST /api/offers/{id}/decisions` — body
  `{request_id: uuid, base_version: int, changes: [{line_id, action: include|exclude|reset, item_code?, size?, quantity?, unit_cost?, note?}]}`.
  Empty `changes` -> 422; a save that changes nothing is a client bug, not a
  version bump. Two changes for the same `line_id` in one batch -> 422; last
  write wins would be silent and order-dependent. Then:
  1. `request_id` already stored -> if `body_sha256` matches, return the
     stored receipt unchanged (retry-safe); if it differs -> 422
     "request_id reused with different changes". This check runs **before**
     the version check on purpose: a retry must succeed even though the
     version has moved on, because its own earlier commit is what moved it.
  2. Bump with a conditional update:
     `UPDATE offers SET version = version + 1 WHERE id = :id AND version = :base`.
     Read-then-write loses updates under Postgres READ COMMITTED, and doing the
     bump first also locks the offer row for the rest of the transaction.
     SQLite serialises writers, so tests alone would never expose this.
  3. `rowcount == 0` -> **re-check `save_requests` for this `request_id`
     before concluding anything.** Two copies of one retry can both miss
     step 1; only one wins the conditional update, and the loser must not be
     told 409 for work its own twin just committed. Roll back, read
     `save_requests` afresh, then: receipt with a matching hash -> 200 with
     that receipt; receipt with a different hash -> 422; no receipt -> 409
     `{current_version}`, nothing written.
  4. Validate every change (below). Any failure -> 422 listing each error by
     line_id, nothing written (the rollback undoes the bump too — atomic).
  5. Replace decisions for the touched lines (delete, then insert; `reset` is
     the delete alone — portable across SQLite and Postgres, and atomic inside
     the transaction), store the receipt under `request_id`, commit. If two
     retries still collide on the `save_requests` primary key, catch
     `IntegrityError`, roll back, and return the stored receipt.

  Only the referenced `line_id`s are loaded for validation, never all 5,000.
- `GET /api/offers/{id}/export.xlsx` — from saved state; the UI's download.
- `GET /api/offers/{id}/export.csv` — same rows, exact decimal strings.
- `GET /api/offers/{id}/source` — original uploaded file.
- `GET /api/config` — `{fault_injection: bool}`, so the UI can show the test
  toggle only where it is enabled. (`/api/health` stays the deploy check.)

### Server-side validation for `include`

Applied to the *effective* values (override if given, else parsed):
item code 1–64 chars after trim; size 1–64 chars after trim, same rule as
item code; quantity is a JSON integer 1..10,000,000; unit cost a decimal
string > 0, max 4 decimal places, <= 1,000,000. Unknown `line_id` -> error.
`exclude` and `reset` need only a valid `line_id`.

`size` is overridable so a row with a blank Size cell can be fixed. Without
it such a row is a dead end: flagged `needs_decision`, offered for repair,
then refused on include.

Value overrides are refused on `exclude` and `reset`. On an exclude they
would be stored without ever being checked against the include rules and
would then apply silently if the line were later included; a reset clears the
whole decision, so it carries nothing at all. An `exclude` may carry a
`note`. A cost override may be written the way a supplier writes it
(`"$4.50"`, `"1,200.50"`) — the same parser the sheet goes through — and is
stored normalised in units.

"JSON integer" is enforced **twice, deliberately**: `StrictInt` at the API
layer and an explicit type check inside `validate_change`. Pydantic's default
lax mode accepts `"12"` -> 12, `true` -> 1 and `12.0` -> 12, none of which is
an integer a supplier typed. The rule must hold even if a future endpoint
forgets the strict annotation.

Every 422 from this endpoint uses one shape,
`{"errors": [{"line_id": ..., "messages": [...]}]}`. Pydantic rejects a
`StrictInt` violation before any handler runs, so a `RequestValidationError`
handler maps its `loc` (`("body", "changes", 2, "quantity")`) back to that
change's `line_id` and re-emits it in that shape. One contract, whichever
layer caught the problem.

These rules must hold even when the UI is bypassed — there is a required test
for exactly that.

### Fault injection (for the required failure demo)

Only when env `FAULT_INJECTION=1`. Header `X-Fault: before-commit` -> roll
back and return 503. Header `X-Fault: after-commit` -> commit, then return
503 (the dangerous case: saved, but the client thinks it failed). A retry with
the same `request_id` must return the stored result and must not re-apply.

## Export (CSV)

Two formats, both built from the same `export_records()` so they cannot
drift apart. Included lines only, from saved state, no totals row.

Fields: `offer_id, supplier, item_code, description, size, category,
quantity, unit_cost_usd, line_value_usd, retail_unit_usd, retail_line_usd,
source_row, notes`. Retail is **two explicit columns**: a single "retail
reference" next to the unit cost reads like a unit price when it is the line
total. `notes` is the line's fixed notes, then its warnings, then the user's
note, joined by `"; "`.

**`export.xlsx` is the primary download and what the UI button links to.**
Excel reads a CSV column of digits as a number, so `000101` opens as `101` —
the exact trap the parser exists to survive, undone at the last step. In the
workbook the item code is a **text cell** (`data_type "s"`, number format
`@`) and stays `000101`; size is text too, so `1/2` is not read as a date.
Money is numeric with format `"$"#,##0.00##` (2–4 decimals, so a $0.0125 cost
isn't shown as $0.01) so the columns can be summed. Headers are human labels,
and both retail ones say "reference only".

openpyxl turns a string starting with `=` into a **formula**, so every text
cell is forced back to `data_type = "s"` after assignment — written as an
inline string, no apostrophe needed.

**`export.csv` stays** for imports and as the byte-exact artifact: it writes
every amount as its exact decimal string. A spreadsheet's numeric cell is an
IEEE double by definition, and openpyxl serialises with `"%.16g" %`, which
coerces a `Decimal` through float (`Decimal("999999.9999")` lands as
`999999.9999000001`). Our code never builds a float — it hands openpyxl the
exact `Decimal` from `money.to_decimal` — and a test reads the workbook back
and checks every amount against the summary. CSV text cells are still
prefixed with `'` when they start with `=`, `+`, `-`, `@`, tab or carriage
return; UTF-8 with BOM.

Tests must prove, for **both** formats: sum of line values == summary cost
total, sum of quantity == summary pieces, and `000101` survives the workbook
as text.

## Frontend behaviour

- Home: upload, recent offers.
- Offer page `/offers/{id}`: summary (lines, pieces, **supplier cost total**,
  and a visually separate **retail reference** labelled "not what we pay");
  tabs: Needs decision, Warnings, Auto-fixed, Excluded, All lines; notices
  (skipped rows, ignored columns).
- Each line expands to show the original cells (coordinate + raw value) beside
  the cleaned value, its issues, and actions: include (editable code / size /
  pieces / cost), exclude, reset. Conflict groups are shown together with
  "keep this one".
- **"Keep this one"** on a conflict or duplicate group sends ONE save holding
  `include` for the chosen line and `exclude` for every sibling in
  `related_line_ids`, so the whole group resolves in a single version bump.
  Resolving a group must never leave a sibling sitting in Needs decision —
  including row 14 of an A108 conflict has to close row 15 in the same save.
- Tabs are filters, not a partition: a line can appear in several. **Excluded
  lists everything currently out of the totals**, including lines still
  awaiting a decision, each with its reason — "what was left out and why" has
  one honest answer in one place.
- **Every value on screen comes from the API.** No sample data, no hardcoded
  item codes, costs or cell references anywhere in the frontend.

### Saving: three pieces of state, never conflated

| | Holds |
| --- | --- |
| `staged` | edits made but not submitted |
| `inFlight` | `{requestId, changes}` — the exact batch being saved or failed |
| `status` | `idle · saving · saved(vN) · failed · conflict` |

A bar shows "N unsaved changes · Save". Then:

- **saving -> 200**: refetch the offer *before* reporting success, so totals
  and lines always come from one response; then `staged` clears and the
  `requestId` is discarded. A new id is minted only after a confirmed success.
- **failed** (5xx/network) -> "Not saved. Your changes are still here. Retry".
  Retry resends `inFlight` byte-identically — a changed body would hit the
  server's "request_id reused with different changes" 422. Editing stays
  allowed, but **while status is failed the only save action is Retry**;
  new edits collect in `staged` and become the next batch only once the
  retry succeeds.
- **422** -> move `inFlight` back into `staged`, show each message against
  its line, and **discard the `requestId`**: the body will change once the
  user fixes the values, so the id must not be reused.
- **409** -> "Changed in another window. Reload latest", then offer to
  re-apply. Before re-applying, compare each pending line's decision in the
  freshly loaded offer against its decision in the snapshot the user was
  working from. Lines another window changed are **listed by name and need
  confirmation before being overwritten**; unaffected lines re-apply
  directly. Re-applying mints a **new** `requestId` (new base version, new
  body, genuinely a new save).
- `staged`, `inFlight` and the `requestId` are persisted in `sessionStorage`
  keyed by offer id, so a refresh during a failed save can still Retry with
  the same `requestId`. A `beforeunload` warning fires while anything is
  unsaved.
- Export is disabled while there are unsaved changes ("save first so the file
  matches what's saved").
- 5,000 lines: virtualize the All-lines list; don't render hidden rows.
- When `/api/config` reports fault injection on, show a clearly labelled
  "Test mode: fail next save (after commit)" toggle. It is **one-shot** —
  it arms the next save only and then disarms, so the retry succeeds and the
  recovery is actually demonstrable. Invisible when the flag is off.

## Evidence the brief requires (deliver all)

1. **Speed:** on the deployed 5,000-row file, time review-ready (upload ->
   page usable), save, export — first and repeat runs. Add `Server-Timing`
   headers per stage (parse, analyze, db, evaluate, serialize) to locate the
   largest delay. Show before/after if optimized; totals must stay identical.
2. **Failure and recovery:** reproduce `after-commit` failure + retry; show
   UI state and DB state before/after (no doubled decisions, version +1 only).
3. **Data protection:** invalid quantity/price posted straight to the API is
   rejected (422) and the saved offer is byte-for-byte unchanged.
4. Tests for data rules and saved-work behaviour.
5. `HANDOFF.md`: workflow, architecture, decisions, storage, assumptions,
   evidence, known gaps, hosting cost, AI usage (what was generated and how it
   was checked).

## Assumptions (state these in HANDOFF.md)

- Blank Harbor size cell = size not offered (no line created, notice shown).
- Rows sharing item+size that agree on quantity, cost and retail are
  duplicates, counted once by default; the copy needs a decision. A differing
  description or category is text, not meaning, so it does not make them a
  conflict — it is named in the message instead.
- Same item+size differing on quantity, cost or retail = conflict; all copies
  excluded until a person chooses.
- If a user's edits leave two included lines sharing item+size, both are
  flagged `DUPLICATE_AFTER_EDIT` (warning, never a block).
- Supplier `Total Units` is a claim to check, never a quantity source.
- Retail is reference only and never enters the supplier-cost total.
- Only `.xlsx`; the first visible sheet with a recognised header is read.

## Non-goals (from the brief — do not build)

Login, real data, Airtable, email, PDF/image extraction, buyer pricing,
commissions, deal management, new file formats, AI-based parsing of values.

## Conventions

- Python 3.14 (local and Docker), type hints, small pure functions in `app/core`. No I/O in core.
- New behaviour gets a test first or alongside. Keep `test_core.py` green.
- Tests run on SQLite by default and against Postgres with
  `TEST_DATABASE_URL=postgresql+psycopg://...`. The version race only exists
  on Postgres, so the race tests mean little until they run there.
- **`conftest.py` refuses any database whose name lacks "test".** The
  fixtures drop every table; pointing them at a real database would be
  unrecoverable. SQLite connects with `timeout=30` so the threaded tests wait
  for the write lock instead of failing with "database is locked".
- **`DATABASE_URL` is validated before use** (`validate_database_url` in
  `db.py`). A `jdbc:` url, the retired `postgres://` scheme or anything
  unparseable stops startup with advice — including how to translate it and
  `unset DATABASE_URL` — instead of a traceback from inside SQLAlchemy. This
  matters because a global `DATABASE_URL` exported by an unrelated project is
  inherited by every command here: **run the server and any measurements with
  `env -u DATABASE_URL`**, and set `DATABASE_URL` explicitly only for Neon.
- Prefer creating new files over large rewrites of working ones.
- Brute-force-simple first; optimize only with a measurement showing why.
- Explain architecture before implementing anything non-trivial, then build.
- Never mark a phase done without pasting the test run output.
