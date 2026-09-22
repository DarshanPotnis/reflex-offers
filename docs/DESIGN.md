# Design

How this tool decides what a supplier's spreadsheet means, and why it is built
the way it is.

---

## 1. The core principle

Every questionable cell is sorted by one question:

> **Does fixing it change what the number *means*, or only how it was
> *written*?**

Changing how something is written is safe to do automatically, as long as it
is shown. Changing what it means is a person's decision. That single test
produces four outcomes, and everything else in the system follows from them.

| Kind | Meaning | Default |
| --- | --- | --- |
| `fixed` | Form changed, meaning certain — `"$3.25"`, `" 45 "`, `"1,200"`, leading zeros | Included, change shown, reversible |
| `warning` | Usable, but worth a look — cost above retail, supplier total disagrees, kept duplicate, duplicate created by an edit | Included, flagged |
| `auto_excluded` | Unambiguously not stock — 0 pieces | Excluded, reason shown |
| `needs_decision` | Could change a quantity or a price — missing, invalid or negative values; duplicate copies; conflicting rows; missing code | Excluded until a person decides |

The policy this enforces: **never overstate what is available, never guess a
price, never invent an identifier.** An offer is therefore always safe to
save with open questions in it — the total is what is *certain*, and the rest
is visible and waiting.

### Duplicates versus conflicts

Rows are grouped on (`item_code`, `size`), case-insensitively.

- **Duplicate** — the members agree on `quantity`, `unit_cost` and `retail`.
  The numbers are not in dispute, so a differing description or category is
  text, not meaning. The first row is kept and flagged; each copy needs a
  decision, so stock is never counted twice. Where the text differs, the
  message names the difference.
- **Conflict** — the members disagree on `quantity`, `unit_cost` or `retail`.
  All of them are excluded until a person picks one.

Only quantity, cost and retail decide which it is. This was originally
stricter — any difference at all, including a typo in a product name, made it
a conflict and dropped both rows. That contradicted the core principle: a
description does not change a number.

---

## 2. Reading the sheets

Two layouts, recognised by **column name**, never by position, so reordering
columns changes nothing:

| Layout | Shape | Required columns |
| --- | --- | --- |
| Northstar | One row per item and size | Item Code, Size, Units Available, Cost USD |
| Harbor | One row per style, one column per size | Style, Unit Cost USD, and at least one size column |

Anything else is refused with a message naming the columns we looked for.
Title rows above the header and footer notes below the data are skipped and
reported, never parsed as items.

Each line gets a stable id — Northstar `R{row}`, Harbor `R{row}-{SIZE}` — and
those ids are the key every decision is recorded against.

**A blank Harbor size cell means that size is not offered.** No line is
created and a notice says so. **The supplier's own `Total Units` is a claim to
check, never a quantity we use**: if it disagrees with the sizes, both rows get
a warning and the sizes win.

### Money

Every amount is an **integer number of ten-thousandths of a dollar**. $4.50 is
`45000`. No float touches a price anywhere — not in the database, not in JSON,
not in the browser.

Why not floats: `0.1 + 0.2 != 0.3` in binary floating point, so a 5,000-line
total drifts by a cent and the screen stops agreeing with the export. Why not
cents: supplier costs like $0.125 per piece are real, and four decimal places
covers them without rounding anyone's price.

The parser **refuses to round**. More than four decimal places is a data
problem to surface, not something to silently change — rounding someone's
price by a hundredth of a cent is still changing their price.

---

## 3. The answer key

These numbers were verified by hand, cell by cell, against the supplied files.
Every test asserts them, and the measurement script re-checks them on every
run and refuses to report timings for an offer whose totals have drifted.

| File | Included lines | Pieces | Supplier cost | Retail reference |
| --- | --- | --- | --- | --- |
| 01 Northstar | 6 | 1,733 | 4208.00 | 21552.00 |
| 02 Harbor | 22 | 1,340 | 3740.00 | 16144.00 |
| 03 Northstar (5,000 rows) | 5,000 | 62,444 | 187214.50 | 1031508.00 |

Including the row-14 A108 line at $5.00 gives 1,833 pieces and 4708.00.

The traps these encode, all covered by tests: duplicate `000101` on rows 6 and
10 (counted once); leading zeros preserved; `"$3.25"`; a missing cost on A104;
quantity `-12`; quantity `0`; the same item at two different costs (a conflict
— both excluded); a blank item code; `"1,200"`; `"TBD"`; `" 45 "`; Harbor
`M = TBD`; an all-zero row; a row with no cost; `M = -3` where the supplier's
total says 21 but the usable sizes add to 24.

The 5,000-row file claims every row is valid, and it is — but reviewers change
files, so a test injects a blank code, a `"TBD"`, a `-5` and a `"$x"` into a
copy and confirms each is caught while the untouched rows still total exactly.

---

## 4. Architecture

```
                    browser (React + TypeScript)
                              │
                     one GET per screen
                              │
┌─────────────────────────────▼──────────────────────────────┐
│  FastAPI                                                   │
│                                                            │
│  api.py        HTTP only. Computes nothing.                │
│  service.py    The only module that commits.               │
│  core/         Pure logic, no I/O:                         │
│      money       integer units, refuses to round           │
│      normalize   cell -> typed value + what changed        │
│      reader      workbook -> lines, by column name         │
│      rules       classify, group duplicates and conflicts  │
│      evaluate    lines + decisions -> effective + totals   │
│      export      CSV and XLSX from evaluated lines         │
└─────────────────────────────┬──────────────────────────────┘
                              │
                   Postgres (Neon) / SQLite
```

One deployable service. The frontend is built into static files that FastAPI
serves, with an `index.html` fallback so `/offers/{id}` survives a refresh.

### Single source of truth

The server computes every total, validates every edit, and builds both exports
from the **saved** state using the same `evaluate()` function the screen uses.
The frontend never computes money; it displays the strings the server returns.

That is what guarantees "the download agrees with the screen" — not
discipline, but structure. There is no second code path that could drift.

`evaluate()` takes a storage-neutral `StoredLine`: exactly the fields the
database holds, money already in integer units. The upload path adapts the
parser's output into it once; every later read builds it from the database.
Same function, same numbers, and the workbook is never re-read.

---

## 5. Storage

| Table | Holds |
| --- | --- |
| `offers` | id (the shareable link), version, supplier, layout, notices, source hash, upload key |
| `offer_sources` | the original uploaded file, so a reviewer can check against it |
| `offer_lines` | one row per parsed line: values, statuses, notes, original cells, issues. **Written once at upload, never edited** |
| `decisions` | one row per line a person has decided. **Upserted, never appended** |
| `save_requests` | one row per accepted save, keyed by `request_id` |

The split between `offer_lines` and `decisions` is deliberate: the parse is a
record of what the supplier sent and never changes, so the original stays
visible beside every correction, and a reset is a clean delete rather than an
attempt to reconstruct history.

Because decisions are upserted, applying the same save twice cannot double
anything — even a bug could not produce two rows for one line.

---

## 6. The save protocol

Two separate problems, two separate mechanisms. Conflating them is where this
normally goes wrong.

- **`request_id`** answers *"have I already done this work?"*
- **`base_version`** answers *"is this work still based on what the user
  saw?"*

The whole contract is four rows:

| `request_id` stored? | Body hash | `base_version` | Result |
| --- | --- | --- | --- |
| no | — | current | Apply, version + 1, store receipt → **200** |
| no | — | stale | **409** with the current version, nothing written |
| yes | matches | *irrelevant* | Return the stored receipt, write nothing → **200** |
| yes | differs | *irrelevant* | **422** "request_id reused with different changes" |

**The `request_id` check runs before the version check, on purpose.** A retry
after a save that committed but failed to reach the client carries a
`base_version` that is stale *because its own earlier commit made it stale*.
Check the version first and every such retry gets a 409, the user reloads and
re-applies, and the change lands twice — the exact bug this protocol exists to
prevent.

Inside one transaction:

1. Stored `request_id`? Return its receipt, or refuse if the body differs.
2. Bump with a conditional update:
   `UPDATE offers SET version = version + 1 WHERE id = :id AND version = :base`.
   This both detects a stale base and takes the row lock that serialises
   everything after it. A plain read-then-write loses updates under Postgres
   READ COMMITTED; SQLite serialises writers, so tests alone would never
   expose it.
3. If that matched nothing, **re-check `save_requests` before concluding
   anything** — two copies of one retry can both miss step 1, and the loser
   must not be told 409 for work its own twin just committed.
4. Validate every change. Any failure rolls back everything, including the
   bump.
5. Replace the decisions for the touched lines, store the receipt, commit.

A save returns a small receipt, `{offer_id, request_id, version, applied}`.
The client re-fetches, so totals and lines always arrive in one response and
cannot disagree. Upload returns a receipt too, `{offer_id, version}` — the
client navigates and fetches the offer there.

### Validation

Applied to the *effective* values (the override if given, otherwise what was
parsed):

| Field | Rule |
| --- | --- |
| item code | 1–64 characters after trimming, must be present |
| size | 1–64 characters after trimming, must be present |
| quantity | a JSON integer, 1 to 10,000,000 |
| unit cost | a decimal string above $0, at most 4 decimal places, at most $1,000,000 |

**"JSON integer" is enforced twice, deliberately** — a strict type at the API
layer and an explicit check in the pure-logic layer. Pydantic's default mode
accepts `"12"` → 12, `true` → 1 and `12.0` → 12, none of which is an integer a
supplier typed. The rule has to hold even if a future endpoint forgets the
annotation.

Prices arrive as decimal **strings** because a JSON float cannot represent
`0.1` exactly. `"$4.50"` and `"1,200.50"` are accepted — the same parser the
spreadsheet goes through, because someone fixing a cost should be able to type
it the way the supplier wrote it.

---

## 7. Export

Two formats, both built from the same records so they cannot drift apart.
Included lines only, in sheet order, no totals row.

`offer_id, supplier, item_code, description, size, category, quantity,
unit_cost_usd, line_value_usd, retail_unit_usd, retail_line_usd, source_row,
notes`

**Retail is two explicit columns.** A single "retail reference" next to the
unit cost reads like a unit price when it is in fact the line total.

**`.xlsx` is the primary download.** Excel reads a CSV column of digits as a
number, so item code `000101` opens as `101` — the exact trap the parser
exists to survive, undone at the last step. In the workbook the code is a text
cell and stays `000101`; size is text too, so `1/2` is not read as a date.
Money is numeric with a currency format so columns can be summed.

openpyxl turns a string starting with `=` into a *formula*, so every text cell
is forced back to a string type — a supplier description of `=HYPERLINK(...)`
would otherwise be live on open. The CSV defuses the same attack with a
leading apostrophe, covering `=`, `+`, `-`, `@`, tab and carriage return.

**`.csv` remains the byte-exact artifact**: it writes every amount as its exact
decimal string. A spreadsheet's numeric cell is an IEEE double by definition,
so the workbook is a rendering, not the source of truth.

**The workbook explains itself.** It opens on a Summary sheet — offer link,
supplier, source file, the saved version it was built from and when, the
totals, and every line left out with its reason — so a file forwarded on its
own still says what it is. Notes are worded for a file rather than the
screen: no "enter one to include this line", no "counted once" after both
copies were counted, and any value a reviewer typed says so, beside what the
sheet held.

---

## 8. Assumptions

- A blank Harbor size cell means that size is not offered — no line, notice
  shown.
- Rows sharing item and size that agree on quantity, cost and retail are
  duplicates, counted once by default; the copy needs a decision. A differing
  description or category does not make them a conflict.
- Rows sharing item and size that differ on quantity, cost or retail are a
  conflict; all copies are excluded until a person chooses.
- If a person's edits leave two *included* lines sharing item and size, both
  are flagged — a warning, never a block, since two real lots are possible.
- The supplier's `Total Units` is checked, never used as a quantity source.
- Retail is reference only and never enters the supplier-cost total.
- Only `.xlsx`; the first visible sheet with a recognised header is read.
- No login. Anyone with an offer link can edit it — see the gaps in
  [HANDOFF.md](../HANDOFF.md).

---

## 9. Decisions log

Each of these changed the design during the build, and each has a reason worth
keeping.

| Decision | Why |
| --- | --- |
| Money as integer ten-thousandths, never float | A cent of drift across 5,000 lines breaks the guarantee that the export matches the screen |
| Parse frozen in `offer_lines`, edits in `decisions` | The original stays visible beside every correction; reset is a delete, not a reconstruction |
| Conditional `UPDATE … WHERE version = :base` | Read-then-write loses updates on Postgres, and SQLite would never reveal it in tests |
| `request_id` checked before `base_version` | A retry's own commit is what made its base stale; checking version first causes the double-apply it is meant to prevent |
| Re-check `save_requests` when the bump matches nothing | Two copies of one retry can both miss the first check; the loser must not get a 409 for work its twin committed |
| Receipts, not full offers, from saves and uploads | The client re-fetches anyway; returning the offer meant reading 5,000 lines back to build a response that was discarded |
| Text differences never promote a duplicate to a conflict | A description does not change a number, so excluding both rows overstated caution and understated stock |
| `size` is overridable | Otherwise a row with a blank size is a dead end: flagged for repair, then refused on include |
| Value overrides refused on exclude and reset | They would be stored without ever being validated, then apply silently if the line were later included |
| Strict integer quantity enforced in two layers | The API's type checker could be bypassed by a future endpoint; the rule belongs with the rules |
| `.xlsx` as the primary export | A CSV loses the leading zeros the whole parser exists to protect |
| gzip despite a local slowdown | 5.73 MB → 383 kB on a response fetched three times per workflow; the ~4% local CPU cost is real and documented |

---

## 10. Out of scope

Login, real supplier data, Airtable, email, PDF or image extraction, buyer
pricing, commissions, deal management, additional file formats, and AI-based
interpretation of cell values. Values are parsed by explicit rules, because a
rule can be explained to the person whose money it is.
