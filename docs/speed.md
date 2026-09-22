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

Every table states the hardware it ran on. `measure.py` reads `/api/health`
first and prints the container's effective CPU quota, worker count and whether
fault injection is on — because a measurement whose machine is unknown cannot
be compared to anything later, and a plan change that silently failed to apply
is otherwise indistinguishable from one that did nothing.

Each measured set runs against a server started fresh from the current build,
so nothing here is stale code.

The full test suite also runs against the real Neon database — `178 passed in
594.70s`, including the threaded version-race and duplicate-retry tests that
skip on SQLite. The ten minutes are almost entirely round-trip latency from
a laptop in Los Angeles to Ohio; see the table at the bottom. Run 1 is a cold process; runs 2–3 are repeats
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

### Render **Free** + Neon, both in Ohio

`python scripts/measure.py https://<app>.onrender.com --runs 3` · answer key
verified on every run.

| Step | First run | Repeat (median) | Wire | Uncompressed | Server-Timing (first run) |
| --- | ---: | ---: | ---: | ---: | --- |
| upload (parse + store) | 10223 ms | 9184 ms | 63 B | same | parse 3982, analyze 599, db 4706 |
| GET offer (review-ready) | 5518 ms | 5718 ms | 383 kB (gzip) | 5.73 MB | db 1264, evaluate 1389, serialize 494 |
| save one decision | 146 ms | 124 ms | 127 B (gzip) | same | db 51 |
| re-fetch after save | 6163 ms | 5293 ms | 383 kB (gzip) | 5.73 MB | db 1431, evaluate 1297, serialize 1289 |
| export .xlsx | 10581 ms | 10854 ms | 252 kB (gzip) | 297 kB | db 775, evaluate 1187, serialize 8002 |
| export .csv | 4057 ms | 3627 ms | 80 kB (gzip) | 647 kB | db 994, evaluate 2006, serialize 590 |
| **Whole workflow** | **36688 ms** | **34799 ms** | | | |

#### A second run, labelled Starter — invalid pending verification

A later run taken after switching the plan to Starter produced numbers that
match the Free run almost exactly: whole workflow 36192 / 36024 ms against
36688 / 34799 ms, and `serialize` on the .xlsx export 8103 ms against 8002 ms.

A genuine change of instance size cannot leave a CPU-bound stage within 1% of
where it was. **The most likely explanation is that the plan change never took
effect** — the service is Blueprint-managed, so `render.yaml` is authoritative
and a dashboard change to the plan can be reverted on the next deploy.

That table is therefore **not recorded as a Starter measurement**. It is kept
below only as a second sample of Free, and the analysis in this document is
drawn from the two together, since they agree.

| Step | "Starter" run 1 | Repeat | Server-Timing (first run) |
| --- | ---: | ---: | --- |
| upload | 10634 ms | 9949 ms | parse 3707, analyze 1299, db 4908 |
| GET offer | 5090 ms | 5384 ms | db 804, evaluate 1684, serialize 511 |
| save | 131 ms | 127 ms | db 40 |
| re-fetch | 5222 ms | 5446 ms | db 1326, evaluate 1285, serialize 497 |
| export .xlsx | 11038 ms | 11306 ms | db 1408, evaluate 1099, serialize 8103 |
| export .csv | 4077 ms | 3812 ms | db 975, evaluate 2196, serialize 496 |
| **Whole workflow** | **36192 ms** | **36024 ms** | |

`render.yaml` now sets `plan: starter` so the blueprint itself carries the
change, and `/api/health` reports the container's effective CPU quota, which
`measure.py` prints in its header. A future table therefore records the
hardware it ran on and this ambiguity cannot recur.

#### Co-location worked

**The save went from 961 ms to 127 ms.** That step is 7 statements moving 510
bytes — pure round-trip latency, nothing else — so it is the cleanest possible
measure of the distance to the database. Putting the app in Ohio beside Neon
removed about 7/8 of it.

The offer reads did not improve the same way, because their `db` stage was
never latency: it is 2.3 MB of transfer. `db` on the GET fell from 4288 ms to
804 ms, which is the same 2.3 MB over a much shorter wire.

#### The CPU is the new constraint

Render's Free instance is roughly 0.1 vCPU. Every CPU stage scales almost
uniformly against the local baseline:

| Stage | Local | Free | Ratio |
| --- | ---: | ---: | ---: |
| `parse` (upload) | 221 ms | 3982 ms | 18.0× |
| `analyze` (upload) | 64 ms | 599 ms | 9.4× |
| `evaluate` (GET) | 106 ms | 1389 ms | 13.1× |
| `serialize` (GET) | 32 ms | 494 ms | 15.4× |
| `serialize` (.xlsx) | 446 ms | 8002 ms | 17.9× |

A median of **15.4×**. The spread (9.4× to 18.0×) is wider than the second
sample suggested, so treat it as an order-of-magnitude figure rather than a
constant — a single run per configuration is not enough to pin it tighter.

**`serialize` on the .xlsx export — 8002 ms — is now the single largest stage
in the whole workflow**, on its own about a quarter of the total. It is
openpyxl building 5,000 rows of styled cells at roughly 0.1 vCPU.

#### Where the missing wall-clock time goes

The GET's wall clock (5518 ms) is well above the sum of its stages (3147 ms).
`Server-Timing` is written inside the route handler, and `GZipMiddleware`
wraps the application, so **compression runs after the header is already
recorded and is invisible to the stages.** The same is true of sending the
response.

Measuring the gap on every row, using the save row as the network baseline
(127 bytes, nothing of ours to compress, so its 95 ms is round-trip plus TLS):

| Step | Wall | Stages | Gap |
| --- | ---: | ---: | ---: |
| save (127 B) | 146 ms | 51 ms | **95 ms** ← baseline |
| upload (63 B response) | 10223 ms | 9287 ms | 936 ms |
| GET offer (383 kB on the wire) | 5518 ms | 3147 ms | **2371 ms** |
| re-fetch (383 kB) | 6163 ms | 4017 ms | 2146 ms |
| export .xlsx (252 kB) | 10581 ms | 9964 ms | 617 ms |
| export .csv (80 kB) | 4057 ms | 3590 ms | 467 ms |

**A caveat on that baseline row.** `measure.py` labels the 127-byte save
response `gzip`, but our middleware has `minimum_size=1024` and cannot have
compressed it. Either Render's edge added the encoding, or the response
arrived chunked and the script fell back to the decoded length for the wire
size. It does not affect any conclusion here — the row is used only as a
round-trip baseline — but the wire column for small responses should not be
taken literally.

#### Compression explains about a quarter of that gap, not most of it

Measured directly on the real 5,732,959-byte payload, and scaled by the 15.4×
factor above:

| Level | Local | Implied at Free | Bytes out | vs level 6 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 13 ms | ~200 ms | 598,891 | +56.4% |
| 3 | 14 ms | ~216 ms | 457,876 | +19.5% |
| 5 | 25 ms | ~385 ms | 401,915 | +4.9% |
| **6 (current)** | **28 ms** | **~431 ms** | **383,002** | — |
| 9 | 110 ms | ~1694 ms | 352,683 | −7.9% |

So at the configured level 6, compression costs roughly **431 ms of the
2371 ms gap**. Baseline network is 95 ms. That leaves **~1845 ms**, which is
sending 383 kB — an effective throughput of about 1.7 Mbps on this
measurement.

**A note on the hypothesis:** the mechanism was exactly right — compression
does run after `Server-Timing` is recorded, so it is invisible in the stages —
but the middleware is configured at **level 6, not 9**. Had it been 9, gzip
alone would have been ~1694 ms and would have explained most of the gap. At 6
it explains under a fifth.

#### What this implies for lowering the compression level

Dropping 6 → 5 saves ~46 ms of CPU and costs 18,913 more bytes. At the
~1.7 Mbps implied above, those bytes take ~91 ms to send — so on *this* link
the change is **net worse**, by roughly the same margin it saves. It becomes worthwhile
only on a faster instance where CPU is cheaper relative to bandwidth, which is
precisely what the Starter measurement will show. Worth deciding from that
table rather than this one.

The bigger lever is that the response is 5.73 MB before compression at all.

#### Instance sizing, for the record

Measured in a container capped at 0.5 CPU with two uvicorn workers: **158 MiB
idle, 196 MiB after a 5,000-row upload, 243 MiB peak during an .xlsx export.**
Starter's 512 MB holds that comfortably, even with both workers busy.

Two workers, not one: endpoints are sync so FastAPI runs them in a threadpool,
but openpyxl is pure Python and an 8-second export holds the GIL, starving
everything else in the process. With a second worker, `/api/health` answered
in 3.9 ms while an export was running. Set by `WEB_CONCURRENCY`, so it is one
environment variable to change.

Also worth knowing: two workers at 0.5 CPU take **more than 7 seconds to
finish starting**. The container `HEALTHCHECK` allows a 20-second start
period; anything stricter would report a healthy service as failed.

### Render **Starter** + Neon, both in Ohio — hardware verified

Same script, same code, only the instance size changed. The header confirmed
the plan actually applied this time, and that Render set **1 worker**.

_host: 0.5 vCPU · host reports 16 cores (cgroup v2) · 1 worker(s) · fault
injection on_

| Step | First run | Repeat (median) | Wire | Uncompressed | Server-Timing (first run) |
| --- | ---: | ---: | ---: | ---: | --- |
| upload (parse + store) | 4129 ms | 4156 ms | 63 B | same | parse 1634, analyze 272, db 1491 |
| GET offer (review-ready) | 2393 ms | 2417 ms | 383 kB (gzip) | 5.73 MB | db 584, evaluate 480, serialize 264 |
| save one decision | 385 ms | 176 ms | 127 B (gzip) | same | db 116 |
| re-fetch after save | 2512 ms | 2441 ms | 383 kB (gzip) | 5.73 MB | db 527, evaluate 494, serialize 492 |
| export .xlsx | 4179 ms | 4416 ms | 252 kB (gzip) | 297 kB | db 325, evaluate 505, serialize 3055 |
| export .csv | 1656 ms | 1492 ms | 80 kB (gzip) | 647 kB | db 353, evaluate 867, serialize 240 |
| **Whole workflow** | **15254 ms** | **15099 ms** | | | |

**Whole workflow 36.7 s → 15.3 s, a 2.4× improvement with no code change.**

| Step | Free | Starter | |
| --- | ---: | ---: | ---: |
| upload | 10223 ms | 4129 ms | 2.5× |
| GET offer | 5518 ms | 2393 ms | 2.3× |
| save one decision | 146 ms | 385 ms | **0.4×** |
| re-fetch | 6163 ms | 2512 ms | 2.5× |
| export .xlsx | 10581 ms | 4179 ms | 2.5× |
| export .csv | 4057 ms | 1656 ms | 2.4× |
| **Whole workflow** | **36688 ms** | **15254 ms** | **2.4×** |

A 5× nominal CPU quota (0.1 → 0.5 vCPU) returned ~2.4×. The likely reason is
that **Free was never really held to 0.1 vCPU**: burstable instances are
generally allowed to exceed their nominal share when the host is idle, so the
Free numbers flattered it and the real gap between the plans is smaller than
the quotas imply. Worth stating rather than quietly claiming 5×.

**The save is the one step that got worse** — 146 → 385 ms first run, 124 →
176 ms repeat, with its `db` stage going 51 → 116 ms. It is 7 statements
moving 510 bytes, so it is pure round-trip time to Neon and has nothing to do
with instance CPU. With one sample per configuration and Neon's free tier able
to suspend an idle database, this is more likely connection warm-up or
database-side variance than a real regression. It is 176 ms; it is not worth
chasing, but it should not be quietly dropped from the comparison either.

The CPU factor against the local baseline is now **6.5× median** (7.4, 4.2,
4.5, 8.2, 6.5 across the five stages), down from 15.4× on Free.

---

## openpyxl `write_only`: a measured non-improvement

The .xlsx export is the largest single delay (~4.2 s on Starter, ~3 s of it
`serialize`), so `write_only` mode was the obvious next step. It was
implemented, and it does not do what it was expected to do.

A/B on the same 5,000-row offer, same output, five rounds each:

| | Time | Peak Python memory | Bytes out |
| --- | ---: | ---: | ---: |
| normal API | 445 ms | 24.2 MB | 293,652 |
| `write_only` | 455 ms | **1.4 MB** | 293,632 |
| change | **+2.4%** | **−94.3%** | −20 |

**`write_only` is a memory optimisation, not a speed one.** It avoids holding
65,000 live `Cell` objects at once; it does not avoid creating or serialising
them, which is where the time goes.

A profile says exactly that: of ~60,000 cells written, the dominant cost is
`etree_write_cell` — the per-cell XML writing that both modes share.

The ceiling for any styling-based optimisation, measured by writing the same
65,000 cells with **no styles at all**:

| | Time |
| --- | ---: |
| unstyled floor (same cell count) | 319 ms |
| current, fully styled | 472 ms |

Styling costs 154 ms, about a third. So even discarding every format — which
would also discard the text-cell rule that keeps `000101` from opening as
`101`, and is therefore not on the table — openpyxl still needs 319 ms to
write 13 columns × 5,000 rows. **This export cannot be made much faster with
openpyxl.** The real levers are fewer cells or a different writer library
(`xlsxwriter` is typically 2–4× faster for writing), and neither is worth
doing without a reason bigger than one export endpoint. **Not taken** — see
the closing summary.

**Kept anyway, for the memory.** The output is byte-identical in content, all
34 export tests pass unchanged, and 24.2 MB → 1.4 MB per export matters on a
512 MB instance if two exports overlap. But it is recorded here as what it is:
a 2.4% regression in the thing it was meant to improve.

## Worker count: Render was right

Render set `WEB_CONCURRENCY=1` from the instance size, contradicting the two
workers this document previously recommended. The argument for two was that a
multi-second export holds the GIL and starves the process. Measured at 0.5
CPU, with an export running:

| Workers | `/api/health` during an export |
| --- | ---: |
| 1 | 74 ms |
| 2 | 3.4 ms |

Degraded, but 74 ms is nowhere near a health-check timeout — openpyxl releases
the GIL often enough that it never hard-blocks. Splitting 0.5 vCPU between two
processes to recover 71 ms is not a good trade. **`WEB_CONCURRENCY` is no
longer set in `render.yaml`**; Render sizes it, and the app defaults to 1.

## Compression level: 6 is the measured optimum

The Starter table supplies both halves of the trade. Its CPU factor is 6.5×
local, and its wall-clock gap gives the throughput: the GET's gap is 1065 ms,
of which 269 ms is the network baseline (from the 127-byte save row) and
~183 ms is gzip, leaving 613 ms to send 383 kB — about **5.0 Mbps**.

With both numbers, every level can be costed end to end:

| Level | CPU at Starter | Bytes | Time to send | Total | vs level 6 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 84 ms | 598,891 | 958 ms | 1043 ms | +248 ms |
| 3 | 91 ms | 457,876 | 733 ms | 824 ms | +29 ms |
| 5 | 162 ms | 401,915 | 643 ms | 806 ms | +11 ms |
| **6 (current)** | **182 ms** | **383,002** | **613 ms** | **795 ms** | **—** |
| 9 | 715 ms | 352,683 | 564 ms | 1279 ms | +484 ms |

**Level 6 is the minimum**, and the curve is flat around it — level 5 costs
11 ms, level 3 costs 29 ms. Level 9 is badly wrong in one direction (CPU) and
level 1 in the other (bytes). The earlier suspicion that 5 might beat 6 was
right to raise and wrong on the numbers: it loses by about 11 ms.

**But the level barely matters next to having compression at all:**

| | Bytes | Time |
| --- | ---: | ---: |
| No compression | 5,732,959 | ~9173 ms |
| gzip level 6 | 383,002 | ~795 ms |

Compression costs ~182 ms of CPU and saves **~8.4 seconds and 5.35 MB on every
offer fetch**, of which there are three per workflow. That is the decision;
the level is a rounding error against it.

Kept at 6. `compresslevel` is a single argument in `main.py` if a future
instance changes the balance.

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


---

# Summary of the performance work

Six measured tables, three code changes, one measured non-improvement, and one
stopping point.

## Where it ended

| | Free | **Starter** | Local (SQLite) |
| --- | ---: | ---: | ---: |
| Whole 5,000-row workflow | 36.7 s | **15.3 s** | 2.0 s |
| Largest single step | export .xlsx 10.6 s | **export .xlsx 4.2 s** | export .xlsx 0.66 s |

The deployed instance is ~6.5× slower per CPU stage than a laptop, which is
what a 0.5 vCPU shared instance is. Nothing in the profile is anomalous.

## What actually changed the numbers

| Change | Effect | Measured where |
| --- | --- | --- |
| **gzip, level 6** | 5.73 MB → 383 kB per offer fetch; ~8.4 s saved per fetch deployed, at ~182 ms CPU | Local A/B, then costed against the Starter table |
| **Upload returns a receipt** | Stopped re-reading 5,000 rows it had just written: 6 SQL statements → 4, 2.47 MB read → 344 B, response 5.73 MB → 63 B, upload 737 → 413 ms local | Local A/B + `db_profile.py` |
| **Server-Timing attribution fix** | No speed change; a database read was being reported as CPU. 2754 ms of a cross-country run was mislabelled | Found on Neon, pinned by a test |
| **Free → Starter** | 36.7 s → 15.3 s, 2.4×, no code change | Deployed, hardware-verified |
| **openpyxl `write_only`** | **+2.4% time, −94% memory.** Not the speed win it was meant to be | Local A/B, five rounds each |

## What was measured and deliberately not changed

- **`xlsxwriter` for the export.** openpyxl's floor for 65,000 cells with no
  styling at all is 319 ms local against 472 ms styled, so the styling we
  cannot give up — the text-cell rule that keeps `000101` from opening as
  `101` — is only a third of the cost. A different library is the only real
  lever left, and a ~4 s export of an internal file does not justify a new
  dependency.
- **A lower gzip level.** Costed end to end at every level; 6 is the minimum
  and the curve is flat around it.
- **Two uvicorn workers.** A single worker answers `/api/health` in 74 ms
  during an export. Splitting 0.5 vCPU to recover 71 ms is a bad trade.
- **Splitting the 5.73 MB offer payload.** The obvious next structural change
  if offers get much bigger than 5,000 rows, and premature below that.

## The largest remaining delay

**`export .xlsx`, 4179 ms, of which 3055 ms is `serialize`** — openpyxl
writing 13 columns × 5,000 rows. Next lever: `xlsxwriter`, expected 2–4× on
that stage, i.e. roughly 3.0 s → 1.0 s and the whole workflow 15.3 s → 13.3 s.
Not taken, for the reason above.

Second is the **upload at 4129 ms** (parse 1634, db 1491, analyze 272) — the
irreducible cost of reading a real 5,000-row spreadsheet once, plus writing
2.47 MB to Postgres.

## Reading these numbers

Three environments appear in this document and they are not interchangeable:

- **Local, SQLite, loopback** — isolates CPU. Finds slow code; says nothing
  about deployment.
- **Laptop to Neon Ohio** — measures the 2,000 miles, not the application.
  Kept only because it exposed the attribution bug and proved the reads are
  volume-bound rather than round-trip-bound.
- **Deployed, app and database co-located in Ohio** — the real figure.

Every table states the hardware it ran on, because one that did not cost a
whole round of analysis when a plan change silently failed to apply.
