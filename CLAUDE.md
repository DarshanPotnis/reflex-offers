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

`backend/app/core/` is done and covered by `backend/tests/test_core.py` and
`backend/tests/test_evaluate.py` (89 passing tests). Run
`cd backend && python -m pytest -q` before and after every change.

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
  app/db.py, app/models.py, app/service.py, app/api.py, app/main.py
frontend/  React + TypeScript + Vite, served by FastAPI as static files
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
  `body_sha256`, `created_at`. Idempotency for saves. The receipt is small —
  `{offer_id, version, applied}` — never the full offer: the 5,000-row offer
  serialises to ~3.2 MB, so storing it per click would bloat the database and
  hand back stale lines on retry. The client re-GETs after a save.

## API contract

All money in JSON is a **string** decimal (`"4.50"`). Pydantic models use
`extra="forbid"`.

- `POST /api/offers` — multipart `file`, optional header `Idempotency-Key`.
  Same key again -> same offer (200), no second offer. Same key with a
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
  version bump. In ONE transaction:
  1. `request_id` already stored -> if `body_sha256` matches, return the
     stored receipt unchanged (retry-safe); if it differs -> 422
     "request_id reused with different changes". This check runs **before**
     the version check on purpose: a retry must succeed even though the
     version has moved on, because its own earlier commit is what moved it.
  2. Bump with a conditional update:
     `UPDATE offers SET version = version + 1 WHERE id = :id AND version = :base`.
     `rowcount == 0` -> 409 `{current_version}`, nothing written. Read-then-write
     loses updates under Postgres READ COMMITTED, and doing it first also locks
     the offer row for the rest of the transaction. SQLite serialises writers,
     so tests alone would never expose this.
  3. Validate every change (below). Any failure -> 422 listing each error by
     line_id, nothing written (the rollback undoes the bump too — atomic).
  4. Upsert decisions (`reset` deletes), store the receipt under
     `request_id`, commit. A concurrent retry of the same `request_id` will
     hit the `save_requests` primary key: catch `IntegrityError`, roll back,
     and return the stored receipt.
- `GET /api/offers/{id}/export.csv` — from saved state.
- `GET /api/offers/{id}/source` — original uploaded file.

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

These rules must hold even when the UI is bypassed — there is a required test
for exactly that.

### Fault injection (for the required failure demo)

Only when env `FAULT_INJECTION=1`. Header `X-Fault: before-commit` -> roll
back and return 503. Header `X-Fault: after-commit` -> commit, then return
503 (the dangerous case: saved, but the client thinks it failed). A retry with
the same `request_id` must return the stored result and must not re-apply.

## Export (CSV)

Included lines only, from saved state. Columns: `offer_id, supplier,
item_code, description, size, category, quantity, unit_cost_usd,
line_value_usd, retail_reference_usd, source_row, notes`. Money via
`money.format_amount`. `notes` is the line's fixed notes, then its warnings,
then the user's note, joined by `"; "`.

Prefix text cells starting with `=`, `+`, `-`, `@`, tab or carriage return
with `'` (spreadsheet formula injection — tab and CR are DDE vectors Excel
honours just like `=`). UTF-8 with BOM so Excel opens it cleanly. No totals
row. A test must prove: sum of `line_value_usd` == summary cost total, and
sum of `quantity` == summary pieces.

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
- Edits are staged locally. A bar shows "N unsaved changes · Save". States:
  saving; saved (vN); **failed -> "Not saved. Your changes are still here. Retry"**
  (retry reuses the same `request_id`; a new id is generated only after a
  confirmed success); **409 -> "Changed in another window. Reload latest"**,
  offering to re-apply the unsaved changes on top.
- Export is disabled while there are unsaved changes ("save first so the file
  matches what's saved").
- 5,000 lines: virtualize the All-lines list; don't render hidden rows.

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
- Prefer creating new files over large rewrites of working ones.
- Brute-force-simple first; optimize only with a measurement showing why.
- Explain architecture before implementing anything non-trivial, then build.
- Never mark a phase done without pasting the test run output.
