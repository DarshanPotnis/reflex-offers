# Handoff

**Live app:** `<to be filled in after deploy>`
**Example saved offer:** `<to be filled in after deploy>`
**Submitted commit:** `<to be filled in>`

Source: this repository. Design and reasoning: [docs/DESIGN.md](docs/DESIGN.md).
Setup and deploy: [README.md](README.md).

---

## 1. The workflow

1. **Upload** a supplier line sheet on the home page. Both supplied layouts
   are recognised by column *name*, so reordered columns and title or footer
   rows are fine. Unsupported files are refused with a message naming the
   columns we looked for.
2. **Review.** The offer page opens on **Needs decision**. The summary shows
   what we would pay the supplier, with the retail reference visually
   separate and labelled "not what we pay". Tabs: Needs decision, Warnings,
   Auto-fixed, Excluded, All lines.
3. **Understand.** Every row that is out of the totals says why, in the list
   itself — "No supplier cost", "Quantity is -12", "Duplicate of row 6".
   Expanding a row shows the original cells with their spreadsheet
   coordinates beside the cleaned values, so any line can be checked against
   the sheet. The original file is downloadable from the header.
4. **Decide.** Fix a code, size, quantity or cost and include the line;
   exclude it; or reset it. Conflicting rows are shown as one card — "keep
   this one". Identical rows ask a different question: **count once** (the
   default) or **count both, supplier has separate lots**. Either resolves
   the whole group in a single save.
5. **Save.** Edits are staged locally and saved together. The offer lives at
   a shareable link and reopens with every decision intact in any browser.
6. **Export.** `.xlsx` (primary) or `.csv`. Export is blocked while anything
   is unsaved, with the reason shown beside the button.

---

## 2. Architecture

```
                    browser (React + TypeScript + Vite)
                              │  one GET per screen
┌─────────────────────────────▼──────────────────────────────┐
│  FastAPI (one process, one URL)                            │
│                                                            │
│  api.py        HTTP only. Computes nothing.                │
│  service.py    The only module that opens a transaction.   │
│  core/         Pure logic, no I/O:                         │
│      money       integer ten-thousandths, refuses to round │
│      normalize   cell -> typed value + what changed        │
│      reader      workbook -> lines, by column name         │
│      rules       classify; group duplicates and conflicts  │
│      evaluate    lines + decisions -> effective + totals   │
│      export      CSV and XLSX from evaluated lines         │
│  static/       the built frontend, index.html fallback     │
└─────────────────────────────┬──────────────────────────────┘
                              │
                   Postgres (Neon) / SQLite for dev
```

The server computes every total, validates every edit, and builds both
exports from the **saved** state using the same `evaluate()` the screen uses.
The frontend never computes money. That is what makes "the download agrees
with the screen" structural rather than a thing to remember.

---

## 3. Main decisions

The full log is in [docs/DESIGN.md §9](docs/DESIGN.md). The five that shaped
everything else:

**Money is an integer number of ten-thousandths of a dollar.** Never a float,
anywhere — not in the database, not in JSON, not in the browser. A cent of
drift across 5,000 lines is exactly the failure this tool exists to prevent.
Four decimal places because supplier costs like $0.125 are real. The parser
refuses to round rather than silently changing a price.

**One question decides everything.** Does fixing this change what the number
*means*, or only how it was *written*? Form changes are automatic and shown;
meaning changes wait for a person. Policy: never overstate availability, never
guess a price, never invent an identifier.

**The parse is frozen; decisions are separate and upserted.** `offer_lines` is
written once at upload and never edited, so the supplier's original is always
visible beside a correction and a reset is a clean delete. Because decisions
are upserted rather than appended, applying the same save twice cannot double
anything.

**Two independent mechanisms protect saved work.** `request_id` answers "have
I already done this?"; `base_version` answers "is this still based on what you
saw?" They are checked in that order, deliberately — a retry after a
committed-but-unreported save carries a stale version *because of its own
commit*, and checking version first would cause the double-apply it is meant
to prevent.

**Everything on screen comes from one GET.** Saves and uploads return small
receipts; the client re-fetches. Totals and lines always arrive in the same
response, so they cannot disagree.

---

## 4. Storage

| Table | Rows | Notes |
| --- | --- | --- |
| `offers` | one per upload | `id` is the shareable link; `version` guards concurrent saves |
| `offer_sources` | one per offer | the original file, for checking against |
| `offer_lines` | one per parsed line | written once, never edited; money in integer units |
| `decisions` | one per decided line | upserted; only the fields actually overridden are set |
| `save_requests` | one per accepted save | keyed by `request_id`; makes a retry a lookup |

Tables are created on startup. At this scale a migration tool would be
ceremony — if the schema needed to change in service, Alembic is the next
step, and it is a known gap.

Postgres in production (Neon), SQLite for development and tests. The same test
suite runs against both.

---

## 5. Assumptions

Listed in full in [docs/DESIGN.md §8](docs/DESIGN.md). The ones that change
numbers:

- A blank Harbor size cell means that size is **not offered** — no line is
  created, and a notice says so.
- Rows sharing item and size that agree on quantity, cost and retail are
  **duplicates**, counted once by default; the copy needs a decision. A
  differing description does **not** make them a conflict.
- Rows that differ on quantity, cost or retail are a **conflict** — all
  copies excluded until a person chooses.
- The supplier's `Total Units` is **a claim to check, never a quantity
  source**. Where it disagrees with the sizes, both are flagged and the sizes
  win.
- **Retail never enters the supplier-cost total.**
- Only `.xlsx`; the first visible sheet with a recognised header is read.

---

## 6. Evidence

### Speed — [docs/speed.md](docs/speed.md)

Measured with `scripts/measure.py`, which walks the whole workflow and
**re-checks the answer key on every run**, refusing to report timings for an
offer whose totals have drifted.

Local (Apple Silicon, SQLite, loopback), current build:

| Step | First run | Repeat |
| --- | ---: | ---: |
| upload | 413 ms | 411 ms |
| GET offer (review-ready) | 334 ms | 330 ms |
| save one decision | 7 ms | 4 ms |
| export .xlsx | 630 ms | 633 ms |
| **Whole workflow** | **1995 ms** | **2005 ms** |

Two improvements, both with before/after numbers in the doc:

- **gzip** cut the offer response from 5.73 MB to **383 kB (93%)**. It made
  the *local* wall clock ~4% worse, because loopback has no bandwidth to save
  and compression costs CPU. Kept because the deployed link is what matters,
  and recorded honestly rather than shown as a win.
- **Upload stopped reading back what it just wrote.** It returned the full
  offer, which meant re-reading all 5,000 stored lines to build a response the
  UI discarded a moment later — it navigates and fetches the offer itself.
  Now a receipt: 6 SQL statements → **4**, 2.47 MB read → **344 B**, response
  5.73 MB → **63 B**, upload 737 ms → **413 ms**.

**Largest delay is now `export .xlsx` at 630 ms**, of which ~446 ms is
openpyxl building 5,000 rows of styled cells. Next candidate, not yet taken.

**Laptop-to-Neon numbers are in the doc and clearly labelled as not
representative of deployment.** Measured from Los Angeles against a database
in Ohio, the whole workflow took ~34 s — with `db` at 85–95% of every step
while the CPU stages barely moved from their local values. The code did not
get slower; the database was 2,000 miles away. Deployment puts both in Ohio.
That table is kept because it is what exposed a real bug (below) and because
it proves the round-trips-versus-volume question:

- The save is 7 statements and 510 bytes → **961 ms, essentially all
  latency** (~137 ms per statement).
- The offer read is 3 statements and 2.3 MB → **4288 ms, essentially all
  transfer**.
- At 137 ms per statement, 5,000 individual INSERTs would take **over 11
  minutes**. The upload uses one `executemany`;
  `test_five_thousand_lines_are_inserted_in_batches` asserts it directly:
  `5,000 lines -> 3 INSERT statement(s)`.

**Deployed, Render Free + Neon both in Ohio** (full table and analysis in
[docs/speed.md](docs/speed.md)):

| Step | First run | Repeat |
| --- | ---: | ---: |
| upload | 10223 ms | 9184 ms |
| GET offer | 5518 ms | 5718 ms |
| save one decision | **146 ms** | 124 ms |
| export .xlsx | 10581 ms | 10854 ms |
| **Whole workflow** | **36688 ms** | **34799 ms** |

Two things this settles:

**Co-locating the app with the database worked.** The save — 7 statements
moving 510 bytes, so pure round-trip latency — fell from **961 ms to ~130 ms**
once both sat in Ohio. That step is the cleanest available measure of distance
to the database, and about 7/8 of it was distance.

**The Free instance's CPU is now the constraint**, at roughly 0.1 vCPU. Five
independent stages scale by a **15.4× median** against the local baseline —
an instance-size effect rather than anything in the code. The largest single
stage in the whole workflow is the `.xlsx` export's `serialize` at **8002 ms**,
openpyxl building 5,000 styled rows.

The wall clock also exceeds the reported stages by ~2.4 s on the big reads,
because `Server-Timing` is recorded inside the route while compression and
response transmission happen after it. Measured on the real payload, gzip at
the configured level 6 accounts for ~431 ms of that; the rest is sending
383 kB at about 1.7 Mbps.

A second run taken after switching the plan to Starter came back within 1% of
Free on every CPU-bound stage, which a real change of instance size cannot do.
The service is Blueprint-managed, so `render.yaml` — which still said
`plan: free` — was overriding the dashboard. It now says `plan: starter`, and
`/api/health` reports the container's CPU quota so a future table states its
own hardware.

**Deployed — Render Starter + Neon, both in Ohio**, hardware verified by the
measurement header (`0.5 vCPU · 1 worker`):

| Step | First run | Repeat | vs Free |
| --- | ---: | ---: | ---: |
| upload | 4129 ms | 4156 ms | 2.5× |
| GET offer (review-ready) | 2393 ms | 2417 ms | 2.3× |
| save one decision | 385 ms | 176 ms | — |
| export .xlsx | 4179 ms | 4416 ms | 2.5× |
| **Whole workflow** | **15254 ms** | **15099 ms** | **2.4×** |

**36.7 s → 15.3 s with no code change.** A 5× nominal CPU quota returning 2.4×
suggests Free was bursting above its nominal 0.1 vCPU, so the Free numbers
flattered it. The save is the one step that did not improve (124 → 176 ms
median); it is 7 statements of pure round-trip time to Neon, unrelated to
instance CPU, and with one sample per configuration that is more likely
connection warm-up than a regression.

**A 5,000-row offer is review-ready in ~2.4 s and exports in ~4.2 s.**

### What changed the numbers, and what did not

| Change | Effect |
| --- | --- |
| gzip level 6 | 5.73 MB → 383 kB per fetch; ~8.4 s saved per fetch deployed for ~182 ms of CPU |
| Upload returns a receipt | Stopped re-reading 5,000 rows it had just written: 6 SQL statements → 4, 2.47 MB read → 344 B |
| Server-Timing attribution fix | No speed change — a database read was being reported as CPU |
| Free → Starter | 2.4×, no code change |
| openpyxl `write_only` | **+2.4% time, −94% memory** — a memory optimisation, not the speed win intended |

`write_only` is recorded honestly as a non-improvement. It avoids *holding*
65,000 `Cell` objects, not creating or serialising them, and the per-cell XML
writing is where the time goes. Kept for the 24 MB → 1.4 MB.

**The compression level was costed end to end** rather than guessed. At the
Starter instance's 6.5× CPU factor and the ~5.0 Mbps its wall-clock gap
implies, level 6 is the measured minimum: level 5 costs 11 ms more, level 3
costs 29 ms, level 9 costs 484 ms. The level is a rounding error next to
having compression at all.

### Largest remaining delay, and the next lever

**`export .xlsx` at 4179 ms, 3055 ms of it openpyxl** writing 13 columns ×
5,000 rows. The next lever is **`xlsxwriter`**, typically 2–4× faster for
writing — roughly 3.0 s → 1.0 s on that stage, and 15.3 s → 13.3 s overall.

**Not taken, deliberately.** openpyxl's floor for the same 65,000 cells with
*no styling at all* is 319 ms local against 472 ms styled, so the styling we
cannot give up — the text-cell rule that keeps `000101` from opening as `101` —
accounts for only a third of the cost. The rest is the library writing cells.
Replacing it means a second spreadsheet dependency and a re-verification of
every export guarantee, and **a ~4 s export of an internal file is adequate**.
It is written down here so the next person starts from the measurement rather
than from scratch.

Performance work stopped here.

### Failure and recovery — [docs/failure-demo.md](docs/failure-demo.md)

Screenshots of all four states plus database state throughout. With
`FAULT_INJECTION=1`, `X-Fault: after-commit` commits and *then* returns 503 —
the dangerous case, where clicking Save again is what would double the stock.

The version column reads **1 → 2 → 2 → 2 → 2** across the failure, the retry,
and two further retries. One decision row throughout. The UI recovers even
after a full page refresh, because the pending batch and its `request_id` are
persisted per offer.

### Data protection — [docs/data-protection.md](docs/data-protection.md)

Eleven invalid payloads sent straight to the API — negative, zero, fractional
and string quantities; unreadable, negative, zero, over-precise and
float-typed prices; an unknown line; and one bad change inside a batch of
three otherwise-valid ones. All 422, all with messages a person can act on,
and the entire offer JSON **byte-identical before and after — same sha256**.

### Tests

```
backend:   172 passed, 6 skipped   (SQLite)
           178 passed in 594.70s   (Neon Postgres — the 6 skips run here)
frontend:  67 checks               (real browser, real server)
```

The six skips are threaded race tests; SQLite serialises writers so the race
cannot occur there. The browser check reads its expectations from the API, so
it cannot pass by agreeing with itself.

---

## 7. Known gaps

**No authentication.** Anyone with an offer link can edit it. The brief
excluded login, and the ids are unguessable uuid4, but this is a shareable
link with write access, not a secret.

**No schema migrations.** Tables are created on startup. Changing a column in
service would need Alembic.

**Offer payload grows linearly.** The 5,000-row offer is 5.73 MB of JSON
(383 kB gzipped) and is fetched on load and after each save. At ~20,000 rows
that becomes uncomfortable, and the fix is a lines-summary/detail split or
pagination. Not done: the brief's file is 5,000 rows and speculative
optimisation without a measurement is how systems get complicated.

**`.xlsx` export is the slowest endpoint**, ~3.0 s of openpyxl on the deployed
instance. `write_only` was tried and is a memory fix, not a speed one;
`xlsxwriter` is the real lever. Measurements and reasoning in
[docs/speed.md](docs/speed.md).

**A conflict card shows "— total" for an excluded row's line value**, since an
excluded line genuinely has no line value. It reads slightly oddly in a card
whose purpose is comparing rows. Fixing it properly means the server sending a
would-be value, because the frontend is not permitted to multiply money.

**No audit trail.** Decisions are upserted, so the current state is exact but
there is no history of who changed what and when. Not required here; it would
be a straightforward append-only table beside `decisions`.

**Uploads are capped at 10 MB** and workbooks at 50,000 lines. Both are
deliberate, and both are policy rather than architecture.

**Fault injection is enabled on the review deployment.** It only enables the
`X-Fault` header and a labelled toggle; nothing fails on its own. It should be
removed for real use.

### Polish backlog

Small, deliberately deferred: keyboard shortcuts for include/exclude on the
focused row; bulk actions across a filtered tab; remembering the open tab
across reloads; a print stylesheet for the summary; an empty-state
illustration; and `aria-live` announcements on the save bar so screen-reader
users hear the state change.

---

## 8. Hosting cost

| Service | Plan | Cost | Caveat |
| --- | --- | --- | --- |
| Render web service | **Starter** (set in `render.yaml`) | **$7/month** | Does not sleep |
| Neon Postgres | Free | $0 | Suspends when idle; 0.5 GB storage |
| **Total** | | **$7/month** | |

Free is $0 and works, with two costs: it **sleeps after 15 minutes idle**, so
the first request waits ~30–60 s, and its ~0.1 vCPU makes CPU the bottleneck —
the whole 5,000-row workflow took ~36 s there. Starter removes the sleep and
the review is not spent waiting on cold starts. Set `plan: free` in
`render.yaml` when it is over.

Storage is small: the 5,000-row offer is ~2.5 MB of rows plus a 170 kB stored
source file, so the free tier holds roughly 150 such offers.

**To remove the cold start**, the Render Starter instance is **$7/month** and
does not sleep. Neon's Launch plan is **$19/month** if the database also needs
to stay warm; for this workload the web instance alone is the one that
matters. A reviewer hitting a sleeping free instance sees a slow first load
that is not the application's doing — worth knowing before drawing conclusions
from the deployed timings.

---

## 9. AI usage

**This project was built with Claude Code**, directed through a written design
specification and a phased plan that I authored and maintained: a spec fixing
the core principle, the answer key, the data model, the API contract and the
save protocol, and a sequence of phases each with explicit completion
criteria. Every phase was reviewed against the spec before the next began.
Architecture for anything non-trivial was proposed and approved in writing
before implementation.

Its output was verified, not assumed:

**The answer key was established by hand**, cell by cell, from the supplied
files before any parsing code was written. Those numbers are asserted by the
test suite, re-checked by the measurement script on every run, and were the
standing reference for every subsequent change. A change that moves them is
wrong until proven otherwise.

**Tests carry the load.** 172 on SQLite and 178 against real Postgres, where
the threaded version-race and duplicate-retry tests actually execute. The
browser check drives a real Chromium against a real server and reads its
expected values from the API, so it cannot pass by agreeing with itself.

**The retry and concurrency paths were mutation-tested.** Removing the
`save_requests` re-check makes a duplicate retry return 409 for work its own
twin committed; replacing the conditional version bump with read-then-write
makes a concurrent save silently overwrite. Both failures were reproduced
deliberately to confirm the tests would catch a regression, not merely pass.

**Manual walkthroughs found real problems that tests did not.** Opening an
exported CSV in Excel showed item code `000101` becoming `101` — the exact
trap the parser exists to survive, undone at the last step; the primary export
became `.xlsx` with the code written as a text cell. A duplicate card offering
"keep this one" on both rows asked the wrong question entirely, since
identical rows are a count-once-or-count-both decision. A greyed-out export
button explained itself only in a hover tooltip, which nobody hovers.

**Reported measurements were audited for honesty.** A cross-country run showed
2,754 ms of "evaluate", a stage that is pure CPU. It was not slow code: the
upload path was not passing its timing object through, so a database read was
being billed as computation. The attribution was fixed and a test now fails if
any SQL executes inside the `evaluate` stage. The same scrutiny is why the
gzip result is reported as a local 4% regression rather than only as a 93%
byte reduction.

Reused libraries: FastAPI, SQLAlchemy, Pydantic, openpyxl, React, Vite,
TanStack Virtual, Playwright. No starter template or generated scaffold —
every file here was written for this project.
