# Speed

Measured with `scripts/measure.py`, which walks the whole path an operations
person takes on the 5,000-row sheet — upload, review, save one decision,
re-fetch, download both exports — and reports wall-clock time beside the
server's own `Server-Timing` stages.

```bash
python scripts/measure.py http://localhost:8000 --runs 3
```

**The script re-checks the answer key on every run** (5,000 lines · 62,444
pieces · $187,214.50 · $1,031,508.00 retail) and exits rather than print
timings for an offer whose totals have drifted. A fast number that is also
wrong is worse than a slow one.

Each measured set runs against a server started fresh from the current build,
so nothing here is stale code. Run 1 is a cold process; runs 2–3 are repeats
into a warm one. Every run uploads a new offer, so parsing is never cached.

---

## Local baselines

These are **local** numbers on an Apple Silicon laptop over loopback. They
isolate CPU cost from network cost — useful for finding the slow code, but
not the figures the brief asks for. The deployed numbers go in the section
below, captured with the same script after Phase 5.

### Before compression — local SQLite

`http://localhost:8801` · 3 runs · answer key verified on every run

| Step | First run | Repeat (median) | Wire | Server-Timing (first run) |
| --- | ---: | ---: | ---: | --- |
| upload (parse + store) | 734 ms | 734 ms | 5.73 MB | parse 222, analyze 68, db 112, evaluate 182, serialize 33 |
| GET offer (review-ready) | 304 ms | 309 ms | 5.73 MB | db 88, evaluate 82, serialize 32 |
| save one decision | 7 ms | 4 ms | 127 B | db 4 |
| re-fetch after save | 332 ms | 315 ms | 5.73 MB | db 86, evaluate 80, serialize 68 |
| export .xlsx | 607 ms | 623 ms | 297 kB | db 65, evaluate 81, serialize 450 |
| export .csv | 242 ms | 214 ms | 647 kB | db 66, evaluate 130, serialize 35 |
| **Whole workflow** | **2226 ms** | **2199 ms** | | |

### After compression — local SQLite

> Superseded by the table below: these numbers were taken before the
> Server-Timing attribution fix, so the upload's `evaluate` stage still
> included a database read. Kept because it is the before/after pair for
> compression.


`http://localhost:8802` · 3 runs · answer key verified on every run

| Step | First run | Repeat (median) | Wire | Uncompressed | Server-Timing (first run) |
| --- | ---: | ---: | ---: | ---: | --- |
| upload (parse + store) | 767 ms | 758 ms | 383 kB (gzip) | 5.73 MB | parse 223, analyze 68, db 116, evaluate 182, serialize 32 |
| GET offer (review-ready) | 333 ms | 341 ms | 383 kB (gzip) | 5.73 MB | db 90, evaluate 81, serialize 32 |
| save one decision | 7 ms | 4 ms | 127 B | same | db 4 |
| re-fetch after save | 360 ms | 337 ms | 383 kB (gzip) | 5.73 MB | db 85, evaluate 81, serialize 68 |
| export .xlsx | 610 ms | 641 ms | 252 kB (gzip) | 297 kB | db 64, evaluate 80, serialize 447 |
| export .csv | 251 ms | 223 ms | 80 kB (gzip) | 647 kB | db 66, evaluate 130, serialize 36 |
| **Whole workflow** | **2327 ms** | **2304 ms** | | | |

Totals identical before and after — the answer-key check passed on all six
runs, which is what makes this a fair comparison rather than two unrelated
measurements.

---

## What is actually slow

**The largest single delay is the upload, at ~750 ms.** With the attribution
fixed it splits: `parse` 219 ms (openpyxl reading 5,000 rows), `db` 172 ms
(the insert plus reading the lines back), `evaluate` 104 ms, `analyze` 64 ms.

**The heaviest server stage across the whole run is `serialize` at ~615 ms**,
and ~450 ms of that is one thing: writing the .xlsx. openpyxl builds 5,000
rows of styled cells in Python. That is the single best target if the export
ever needs to be faster, and `write_only` mode would be the way.

**The three 5.73 MB JSON transfers are the thing that will dominate
deployed.** Locally they cost almost nothing, because loopback has no
bandwidth limit. Over a real connection they are the whole story.

## Compression: why it stays despite a local regression

Adding `GZipMiddleware` made the local wall clock **~4% worse** (2226 → 2327
ms). That is honest and expected: there is no network to save over loopback,
so all that is left is the CPU to compress.

What it bought is on the wire:

| Response | Uncompressed | gzip | Reduction |
| --- | ---: | ---: | ---: |
| GET offer (×3 per workflow) | 5.73 MB | 383 kB | **93%** |
| export .csv | 647 kB | 80 kB | 88% |
| export .xlsx | 297 kB | 252 kB | 15% (already a zip) |

A workflow moves ~17.2 MB uncompressed and ~1.15 MB compressed. Whether that
trade is worth ~100 ms of CPU depends entirely on the link, which is why the
decision is confirmed by the deployed numbers below rather than by these.
`compresslevel=6` rather than 9: the last few percent of ratio costs more CPU
than it saves.

## Bulk insert, not 5,000 round trips

`test_five_thousand_lines_are_inserted_in_batches` asserts this directly
rather than inferring it from a stopwatch, because on SQLite the difference
is milliseconds while over a network it is the difference between one round
trip and five thousand:

```
5,000 lines -> 3 INSERT statement(s), 5002 rows, 6 statements in total
```

Three statements: the offer, the stored source file, and one `executemany`
carrying all 5,000 lines. The Neon `db` stage confirms the same thing
against a real network.

### After the attribution fix, still reading back — local SQLite

`http://localhost:8851` · 3 runs · answer key verified on every run

| Step | First run | Repeat (median) | Wire | Server-Timing (first run) |
| --- | ---: | ---: | ---: | --- |
| upload (parse + store) | 737 ms | 747 ms | 383 kB (gzip) | parse 219, analyze 64, **db 172**, **evaluate 104**, serialize 31 |
| GET offer (review-ready) | 325 ms | 328 ms | 383 kB (gzip) | db 84, evaluate 85, serialize 31 |
| save one decision | 6 ms | 4 ms | 127 B | db 4 |
| re-fetch after save | 343 ms | 330 ms | 383 kB (gzip) | db 80, evaluate 79, serialize 63 |
| export .xlsx | 596 ms | 614 ms | 252 kB (gzip) | db 61, evaluate 78, **serialize 438** |
| export .csv | 240 ms | 213 ms | 80 kB (gzip) | db 62, evaluate 124, serialize 35 |
| **Whole workflow** | **2247 ms** | **2237 ms** | | |

### Current — local SQLite, upload returns a receipt

`http://localhost:8871` · 3 runs · answer key verified on every run

| Step | First run | Repeat (median) | Wire | Server-Timing (first run) |
| --- | ---: | ---: | ---: | --- |
| upload (parse + store) | **413 ms** | 411 ms | **63 B** | parse 221, analyze 64, db 108 |
| GET offer (review-ready) | 334 ms | 330 ms | 383 kB (gzip) | db 62, evaluate 106, serialize 32 |
| save one decision | 7 ms | 4 ms | 127 B | db 5 |
| re-fetch after save | 341 ms | 357 ms | 383 kB (gzip) | db 91, evaluate 84, serialize 33 |
| export .xlsx | 630 ms | 633 ms | 252 kB (gzip) | db 85, evaluate 81, **serialize 446** |
| export .csv | 270 ms | 269 ms | 80 kB (gzip) | db 70, evaluate 145, serialize 36 |
| **Whole workflow** | **1995 ms** | **2005 ms** | | |

---

## The `evaluate` stage was lying

A cross-country Neon run reported **2754 ms of `evaluate` on the upload**,
against ~180 ms on local SQLite. `evaluate()` is pure CPU over data already
in memory, so a 15× swing with the same input meant the label was wrong, not
the code.

It was. `api.upload_offer` called `service.load_evaluated(session, offer.id)`
**without passing its `Timings`**, so `load_evaluated` built a throwaway one,
its internal `db` stage was discarded, and the caller's
`with timings.stage("evaluate")` wrapped the whole call — including the two
SELECTs that read all 5,000 stored lines back. On SQLite that hid inside
noise. Over a link to Ohio it was seconds of network reported as CPU.

The same line on the GET and export paths already passed `timings`, which is
why only the upload was wrong.

Fixed by passing `timings` through. The time did not go away — it moved to
the stage that owns it:

| Upload stage | Before the fix | After |
| --- | ---: | ---: |
| `db` | 116 ms | **172 ms** |
| `evaluate` | 182 ms | **104 ms** |

`evaluate` on upload (104 ms) now agrees with `evaluate` on the GET (85 ms),
which is the sanity check: both evaluate the same 5,000 lines, so they should
cost about the same.

`test_no_sql_runs_inside_the_evaluate_stage` pins this. It wraps `Timings`,
counts SQL statements inside each stage, and fails if any run inside
`evaluate`, `parse` or `serialize`. Reverting the fix makes it fail with
`2 SQL statement(s) ran inside the evaluate stage`.

**The honest reading of the Neon number:** roughly 2.7 s of it was reading
2.47 MB back from Ohio, and it is now labelled `db`. Correct attribution, same
wall clock. The work itself is still there to be removed — see below.

## Round trips or volume? — SQL per endpoint

Produced by `scripts/db_profile.py`, which counts every statement the real
app issues and measures what the database had to send. Point `DATABASE_URL`
at a remote database and the per-statement timings become real round-trip
latency.

```bash
python scripts/db_profile.py                          # local SQLite
DATABASE_URL='postgresql+psycopg://…' python scripts/db_profile.py
```

### SQL per endpoint — SQLite, 5,000-row offer

| Endpoint | SQL statements | Statement mix | Bytes read | Bytes written | Bound by |
| --- | ---: | --- | ---: | ---: | --- |
| `POST /api/offers` (upload) | 4 | INSERT×3 SELECT×1 | 344 B | 2.47 MB | **volume** (write) |
| `GET /api/offers/{id}` | 3 | SELECT×3 | 2.30 MB | 0 B | **volume** |
| `POST /{id}/decisions` (1 line) | 7 | DELETE×1 INSERT×2 SELECT×3 UPDATE×1 | 439 B | 71 B | neither — small and quick |
| `GET /{id}/export.xlsx` | 3 | SELECT×3 | 2.30 MB | 0 B | **volume** |
| `GET /{id}/export.csv` | 3 | SELECT×3 | 2.30 MB | 0 B | **volume** |
| `GET /api/offers` (list) | 1 | SELECT×1 | 344 B | 0 B | neither |

_Statement counts are database-independent; the SQLite timings are not
network-bound and are omitted._

**Nothing here is round-trip-bound.** No endpoint issues more than seven
statements, and the upload's three INSERTs carry all 5,002 rows in one
`executemany` — so a slow link multiplies bytes, not statements.

**Every offer-reading endpoint is volume-bound**: three statements moving
~2.3 MB. That is the single fact explaining the laptop-to-Ohio numbers. On
loopback 2.3 MB is free; across a continent it is the whole cost, and it is
paid on the upload (read-back), the GET, the re-fetch after a save, and both
exports.

**The save is the one latency-sensitive endpoint**: seven statements moving
510 bytes, so its cost is ~7 × round-trip latency regardless of offer size.
That is why saving stays fast on a 5,000-row sheet.

## Taken: the upload no longer reads back what it just wrote

The upload used to write 5,000 lines and then **read all 5,000 back** to
build a full-offer response — 2.47 MB of reading, on top of the 2.47 MB it
had just written.

The argument for keeping it was that the response should be built from saved
state like every other endpoint. The argument against turned out to be
decisive: **the UI navigates to the offer page and fetches the offer anyway**,
so that response was discarded every single time. The server log shows it
plainly — `POST /api/offers` followed immediately by `GET /api/offers/{id}`.

`POST /api/offers` now returns a receipt, exactly like a save does:

```json
{"offer_id": "…", "version": 1}
```

"Everything the screen shows comes from one GET" is not weakened by this —
it is strengthened. Nothing is rendered from the upload response at all.

### Before and after

| | Statements | Mix | Bytes read | Response |
| --- | ---: | --- | ---: | ---: |
| Upload, before | 6 | INSERT×3 **SELECT×3** | 2.47 MB | 5.73 MB (383 kB gzipped) |
| Upload, after | 4 | INSERT×3 **SELECT×1** | **344 B** | **63 B** |

The one remaining SELECT is the single offer row needed for the receipt.

| Step | Before | After |
| --- | ---: | ---: |
| upload wall clock | 737 ms | **413 ms** |
| upload `db` stage | 172 ms | **108 ms** |
| upload `evaluate` stage | 104 ms | **gone** |
| upload `serialize` stage | 31 ms | **gone** |
| Whole workflow | 2247 ms | **1995 ms** |

A 44% cut off the upload locally, where bytes are nearly free. On the
laptop-to-Ohio link the upload's `db` was 6188 ms plus 2754 ms mislabelled as
`evaluate`; roughly half of that was the read-back this removes.

**The largest delay is now `export .xlsx` at 630 ms**, of which ~446 ms is
openpyxl building 5,000 rows of styled cells. That is the next candidate, and
`write_only` mode is the obvious approach — but the deployed numbers should
justify it first.

`test_upload_returns_a_receipt_not_the_whole_offer` pins the new contract:
the body has exactly `offer_id` and `version`, is under 200 bytes, and the
response carries no `evaluate` or `serialize` stage at all.

---

## Deployed numbers

> **To be captured after Phase 5**, with the same script against the Render
> URL and its Neon database:
>
> ```bash
> python scripts/measure.py https://<app>.onrender.com --runs 3
> ```
>
> This is the measurement the brief actually asks for. Expected to show the
> compression trade paying off, and the `db` stage on the upload confirming
> the bulk insert.

## Laptop to Neon Ohio — **not representative of deployment**

> **Read this label before the numbers.** These were taken with the server
> running on a laptop in Los Angeles against a Neon database in Ohio — every
> statement crosses the continent and back. Deployment puts the app and the
> database in the same region, so this table measures the distance, not the
> application.
>
> It is recorded because it is what exposed the `evaluate` attribution bug,
> and because it is the clearest demonstration that the offer-reading
> endpoints are volume-bound.
>
> **Measured before the attribution fix**, so the upload's `evaluate` column
> below is really a database read. Not re-run: the deployed, same-region
> numbers are the ones that decide anything.

| Step | First run | Repeat (median) | Wire | Uncompressed | Server-Timing (first run) |
| --- | ---: | ---: | ---: | ---: | --- |
| upload (parse + store) | 9972 ms | 12316 ms | 383 kB (gzip) | 5.73 MB | parse 255, analyze 40, db 6188, evaluate 2754, serialize 35 |
| GET offer (review-ready) | 4944 ms | 4942 ms | 383 kB (gzip) | 5.73 MB | db 4288, evaluate 92, serialize 64 |
| save one decision | 967 ms | 958 ms | 127 B | same | db 961 |
| re-fetch after save | 6019 ms | 5057 ms | 383 kB (gzip) | 5.73 MB | db 5339, evaluate 97, serialize 60 |
| export .xlsx | 7216 ms | 5758 ms | 252 kB (gzip) | 297 kB | db 6262, evaluate 135, serialize 421 |
| export .csv | 4566 ms | 5066 ms | 80 kB (gzip) | 647 kB | db 4005, evaluate 92, serialize 34 |
| **Whole workflow** | **33684 ms** | **34096 ms** | | | |

### What this table proves

`db` is 85–95% of every step. The CPU stages barely move from their local
values — `parse` 255 ms vs 219 local, `evaluate` ~92 ms vs ~85, `serialize`
421 ms vs 438 for the .xlsx. **The code did not get slower; the database got
further away.**

The save is the useful calibration. 7 statements, 510 bytes, 961 ms — about
**137 ms per statement**, which is the round-trip cost to Ohio including
query overhead. Everything else follows from it:

| Step | Statements | × 137 ms latency | Actual `db` | Remainder = transfer |
| --- | ---: | ---: | ---: | ---: |
| GET offer | 3 | ~411 ms | 4288 ms | **~3.9 s for 2.3 MB** |
| export .csv | 3 | ~411 ms | 4005 ms | ~3.6 s |
| save | 7 | ~959 ms | 961 ms | **~0 ms** |

The save is entirely latency and no transfer; the offer reads are almost
entirely transfer. That is the volume-vs-round-trips answer measured rather
than argued, and it matches `db_profile.py` exactly.

It also confirms the bulk insert: the upload writes 5,000 lines across a
continent in one `executemany`. At 137 ms per statement, 5,000 individual
INSERTs would have taken **over 11 minutes**.
