#!/usr/bin/env python3
"""Time the 5,000-row workflow against any running instance.

    python scripts/measure.py http://localhost:8000
    python scripts/measure.py https://your-app.onrender.com --runs 3

Walks the whole path an operations person takes — upload, review, save one
decision, re-fetch, download both exports — and reports wall-clock time
beside the server's own `Server-Timing` stages, so a slow step can be blamed
on the right thing.

Every run re-checks the answer key. A fast number that is also wrong is
worse than a slow one, so the script refuses to report timings for an offer
whose totals have drifted.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "backend/tests/fixtures/03-northstar-5000-rows.xlsx"
)

# From CLAUDE.md. Checked on every run.
EXPECTED_LINES = 5_000
EXPECTED_PIECES = 62_444
EXPECTED_COST = "187214.50"
EXPECTED_RETAIL = "1031508.00"

STAGES = ("parse", "analyze", "db", "evaluate", "serialize")


@dataclass
class Step:
    name: str
    seconds: float
    wire_bytes: int
    body_bytes: int
    timing: dict[str, float] = field(default_factory=dict)
    note: str = ""

    @property
    def ms(self) -> float:
        return self.seconds * 1000


def parse_server_timing(header: str | None) -> dict[str, float]:
    """'parse;dur=223.9, db;dur=104.8' -> {'parse': 223.9, 'db': 104.8}"""
    stages: dict[str, float] = {}
    for part in (header or "").split(","):
        part = part.strip()
        if ";dur=" not in part:
            continue
        name, _, value = part.partition(";dur=")
        try:
            stages[name.strip()] = float(value)
        except ValueError:
            pass
    return stages


def measure(client: httpx.Client, name: str, method: str, url: str, **kwargs) -> tuple[Step, httpx.Response]:
    started = time.perf_counter()
    response = client.request(method, url, **kwargs)
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    wire = int(response.headers.get("content-length") or len(response.content))
    step = Step(
        name=name,
        seconds=elapsed,
        wire_bytes=wire,
        body_bytes=len(response.content),
        timing=parse_server_timing(response.headers.get("server-timing")),
        note=response.headers.get("content-encoding", ""),
    )
    return step, response


def describe_host(base: str, timeout: float) -> str:
    """Which hardware answered. Recorded so a run can be compared later."""
    try:
        with httpx.Client(base_url=base, timeout=timeout) as client:
            health = client.get("/api/health").json()
    except Exception:
        return "host: unknown (/api/health did not answer)"
    limit = health.get("cpu_limit")
    allowed = f"{limit} vCPU" if limit is not None else "no CPU quota"
    return (
        f"host: {allowed} · host reports {health.get('cpu_count')} cores "
        f"({health.get('cpu_limit_source')}) · "
        f"{health.get('workers')} worker(s) · "
        f"fault injection {'on' if health.get('fault_injection') else 'off'}"
    )


def one_run(base: str, timeout: float) -> list[Step]:
    steps: list[Step] = []
    # Accept gzip the way a browser does, so the wire size is the real one.
    headers = {"Accept-Encoding": "gzip, deflate"}
    with httpx.Client(base_url=base, timeout=timeout, headers=headers) as client:
        data = FIXTURE.read_bytes()

        step, response = measure(
            client, "upload (parse + store)", "POST", "/api/offers",
            files={"file": (FIXTURE.name, data, "application/vnd.ms-excel")},
        )
        # A receipt: the client navigates and fetches the offer itself.
        offer_id = response.json()["offer_id"]
        steps.append(step)

        step, response = measure(client, "GET offer (review-ready)", "GET", f"/api/offers/{offer_id}")
        offer = response.json()
        steps.append(step)
        check_answer_key(offer)

        first_line = offer["lines"][0]["line_id"]
        step, response = measure(
            client, "save one decision", "POST", f"/api/offers/{offer_id}/decisions",
            json={
                "request_id": str(uuid.uuid4()),
                "base_version": offer["version"],
                "changes": [{"line_id": first_line, "action": "exclude"}],
            },
        )
        if response.json()["version"] != offer["version"] + 1:
            raise SystemExit("save did not bump the version; refusing to report timings")
        steps.append(step)

        step, response = measure(client, "re-fetch after save", "GET", f"/api/offers/{offer_id}")
        after = response.json()
        steps.append(step)
        if after["summary"]["included_lines"] != EXPECTED_LINES - 1:
            raise SystemExit("re-fetch does not reflect the save; refusing to report timings")

        step, _ = measure(client, "export .xlsx", "GET", f"/api/offers/{offer_id}/export.xlsx")
        steps.append(step)
        step, _ = measure(client, "export .csv", "GET", f"/api/offers/{offer_id}/export.csv")
        steps.append(step)

    return steps


def check_answer_key(offer: dict) -> None:
    summary = offer["summary"]
    actual = (
        summary["included_lines"], summary["pieces"],
        summary["supplier_cost"], summary["retail_reference"],
    )
    expected = (EXPECTED_LINES, EXPECTED_PIECES, EXPECTED_COST, EXPECTED_RETAIL)
    if actual != expected:
        raise SystemExit(
            f"answer key mismatch: got {actual}, expected {expected}. "
            "Refusing to report timings for wrong numbers."
        )


def human_bytes(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f} MB"
    if count >= 1_000:
        return f"{count / 1_000:.0f} kB"
    return f"{count} B"


def render(runs: list[list[Step]], base: str, label: str, host: str) -> str:
    out: list[str] = []
    out.append(f"### {label}")
    out.append("")
    out.append(f"`{base}` · {len(runs)} runs · answer key verified on every run")
    out.append("")
    out.append(f"_{host}_")
    out.append("")

    names = [step.name for step in runs[0]]
    out.append("| Step | First run | Repeat (median) | Wire | Uncompressed | Server-Timing (first run) |")
    out.append("| --- | ---: | ---: | ---: | ---: | --- |")
    for index, name in enumerate(names):
        first = runs[0][index]
        repeats = [run[index].ms for run in runs[1:]]
        repeat = f"{statistics.median(repeats):.0f} ms" if repeats else "—"
        timing = first.timing
        detail = ", ".join(
            f"{stage} {timing[stage]:.0f}" for stage in STAGES if stage in timing
        ) or "—"
        wire = human_bytes(first.wire_bytes)
        if first.note:
            wire += f" ({first.note})"
        body = human_bytes(first.body_bytes)
        same = first.wire_bytes == first.body_bytes
        out.append(
            f"| {name} | {first.ms:.0f} ms | {repeat} | {wire} | "
            f"{'same' if same else body} | {detail} |"
        )

    totals = [sum(step.ms for step in run) for run in runs]
    out.append(
        f"| **Whole workflow** | **{totals[0]:.0f} ms** | "
        f"**{statistics.median(totals[1:]):.0f} ms** | | | |"
        if len(totals) > 1
        else f"| **Whole workflow** | **{totals[0]:.0f} ms** | — | | | |"
    )
    out.append("")

    slowest_step = max(runs[0], key=lambda s: s.ms)
    stage_totals: dict[str, float] = {}
    for step in runs[0]:
        for stage, ms in step.timing.items():
            stage_totals[stage] = stage_totals.get(stage, 0.0) + ms
    if stage_totals:
        worst_stage = max(stage_totals.items(), key=lambda kv: kv[1])
        out.append(
            f"**Largest delay:** `{slowest_step.name}` at {slowest_step.ms:.0f} ms. "
            f"Across the whole run the heaviest server stage is `{worst_stage[0]}` "
            f"at {worst_stage[1]:.0f} ms total."
        )
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--label", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not FIXTURE.exists():
        raise SystemExit(f"fixture not found: {FIXTURE}")

    base = args.base_url.rstrip("/")
    host = describe_host(base, args.timeout)
    print(host, file=sys.stderr, flush=True)

    runs: list[list[Step]] = []
    for index in range(args.runs):
        print(f"run {index + 1}/{args.runs}…", file=sys.stderr, flush=True)
        runs.append(one_run(base, args.timeout))

    report = render(runs, base, args.label or f"Measured against {base}", host)
    print(report)
    if args.out:
        args.out.write_text(report)
        print(f"written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
