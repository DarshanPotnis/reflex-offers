"""Classify every line and choose its default outcome.

The one question behind every rule: does fixing this change what the number
*means*, or only how it was *written*?

  fixed           written differently, meaning certain ("$3.25", " 45 ",
                  "1,200"). Applied automatically, shown, reversible.
  warning         line is usable and included, but something deserves a look
                  (cost above retail, supplier total disagrees).
  auto_excluded   unambiguously not stock (0 pieces). Left out, reason shown.
  needs_decision  anything that could change a quantity or a price
                  (missing/invalid/negative values, duplicates, conflicts).
                  Left out by default until a person decides.

Default policy: never overstate what is available and never guess a price.
An offer is therefore always safe to save, even with open questions.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from .money import format_usd, to_units
from .reader import FIELD_LABELS, SourceLine

IssueKind = Literal["fixed", "warning", "auto_excluded", "needs_decision"]

LARGE_QUANTITY = 100_000


@dataclass(frozen=True)
class Issue:
    code: str
    kind: IssueKind
    message: str
    field: str | None = None


@dataclass
class Analysis:
    line: SourceLine
    issues: list[Issue] = field(default_factory=list)
    related_line_ids: list[str] = field(default_factory=list)

    @property
    def default_included(self) -> bool:
        return not any(i.kind in ("auto_excluded", "needs_decision") for i in self.issues)

    @property
    def needs_decision(self) -> bool:
        return any(i.kind == "needs_decision" for i in self.issues)


def _money(value: Decimal) -> str:
    return format_usd(to_units(value))


def _line_issues(line: SourceLine) -> list[Issue]:
    issues: list[Issue] = []

    for name in ("item_code", "description", "size", "quantity", "unit_cost", "retail", "category"):
        parsed = getattr(line, name)
        if parsed.status == "normalized" and parsed.note:
            issues.append(Issue("NORMALIZED", "fixed", f"{FIELD_LABELS[name]}: {parsed.note}.", name))

    code = line.item_code
    if code.status == "missing":
        issues.append(Issue("MISSING_ITEM_CODE", "needs_decision",
                            "No item code. Enter one to include this line.", "item_code"))
    elif code.status == "invalid":
        issues.append(Issue("INVALID_ITEM_CODE", "needs_decision", f"Item code: {code.note}.", "item_code"))

    if line.size.status != "ok" and not line.size.usable:
        issues.append(Issue("MISSING_SIZE", "needs_decision", "No size given.", "size"))

    qty = line.quantity
    if qty.status == "missing":
        issues.append(Issue("MISSING_QUANTITY", "needs_decision", "No quantity given.", "quantity"))
    elif qty.status == "invalid":
        issues.append(Issue("INVALID_QUANTITY", "needs_decision",
                            f"Quantity isn't usable: {qty.note}.", "quantity"))
    elif qty.value is not None and qty.value < 0:
        issues.append(Issue("NEGATIVE_QUANTITY", "needs_decision",
                            f"Quantity is {qty.value:,}. Pieces can't be negative, so this wasn't guessed.",
                            "quantity"))
    elif qty.value == 0:
        issues.append(Issue("ZERO_QUANTITY", "auto_excluded", "0 pieces available.", "quantity"))
    elif qty.value is not None and qty.value >= LARGE_QUANTITY:
        issues.append(Issue("LARGE_QUANTITY", "warning",
                            f"{qty.value:,} pieces is unusually large for one line. Check the original.",
                            "quantity"))

    cost = line.unit_cost
    if cost.status == "missing":
        issues.append(Issue("MISSING_COST", "needs_decision",
                            "No supplier cost. Enter one to include this line.", "unit_cost"))
    elif cost.status == "invalid":
        issues.append(Issue("INVALID_COST", "needs_decision",
                            f"Supplier cost isn't usable: {cost.note}.", "unit_cost"))
    elif cost.value is not None and cost.value <= 0:
        issues.append(Issue("NONPOSITIVE_COST", "needs_decision",
                            f"Supplier cost is {_money(cost.value)}. It must be above $0.", "unit_cost"))

    retail = line.retail
    if retail.status == "invalid":
        issues.append(Issue("INVALID_RETAIL", "warning",
                            f"Retail reference isn't usable ({retail.note}); left out of the retail total.",
                            "retail"))
    elif retail.value is not None and retail.value < 0:
        issues.append(Issue("NEGATIVE_RETAIL", "warning",
                            "Retail reference is negative; left out of the retail total.", "retail"))
    elif (
        cost.usable and retail.usable and cost.value is not None and retail.value is not None
        and retail.value > 0 and cost.value > retail.value
    ):
        issues.append(Issue("COST_ABOVE_RETAIL", "warning",
                            f"Supplier cost {_money(cost.value)} is above retail {_money(retail.value)}.",
                            "unit_cost"))

    for warning in line.row_warnings:
        issues.append(Issue("SUPPLIER_TOTAL_MISMATCH", "warning", warning, "quantity"))

    return issues


def _signature(line: SourceLine) -> tuple:
    return (
        line.description.value, line.category.value,
        line.quantity.status, line.quantity.value,
        line.unit_cost.status, line.unit_cost.value,
        line.retail.status, line.retail.value,
    )


def _differences(lines: list[SourceLine]) -> str:
    parts: list[str] = []
    for name, label in (("quantity", "pieces"), ("unit_cost", "cost"), ("retail", "retail"),
                        ("description", "description"), ("category", "category")):
        shown: list[str] = []
        for ln in lines:
            p = getattr(ln, name)
            if p.value is None:
                text = "blank" if p.status == "missing" else f"'{ln.cells[name].raw}'" if name in ln.cells else "?"
            elif name in ("unit_cost", "retail"):
                text = _money(p.value)
            elif name == "quantity":
                text = f"{p.value:,}"
            else:
                text = str(p.value)
            shown.append(text)
        if len(set(shown)) > 1:
            parts.append(f"{label} {' vs '.join(shown)}")
    return "; ".join(parts) or "values"


def analyze(lines: list[SourceLine]) -> list[Analysis]:
    results = {ln.line_id: Analysis(ln, _line_issues(ln)) for ln in lines}

    groups: dict[tuple[str, str], list[SourceLine]] = defaultdict(list)
    for ln in lines:
        if ln.item_code.usable and ln.size.usable:
            groups[(ln.item_code.value.casefold(), ln.size.value.casefold())].append(ln)

    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m.source_row)
        rows = ", ".join(str(m.source_row) for m in members)
        code, size = members[0].item_code.value, members[0].size.value
        ids = [m.line_id for m in members]

        if len({_signature(m) for m in members}) == 1:
            first, *copies = members
            results[first.line_id].issues.append(Issue(
                "DUPLICATE_KEPT", "warning",
                f"Identical to row(s) {', '.join(str(c.source_row) for c in copies)}. Counted once here.",
            ))
            for c in copies:
                results[c.line_id].issues.append(Issue(
                    "DUPLICATE_ROW", "needs_decision",
                    f"Identical to row {first.source_row} ({code}, {size}). Left out so stock isn't "
                    "counted twice. Include only if the supplier really has two separate lots.",
                ))
        else:
            for m in members:
                results[m.line_id].issues.append(Issue(
                    "CONFLICTING_ROWS", "needs_decision",
                    f"{code} ({size}) appears on rows {rows} with different values: "
                    f"{_differences(members)}. Choose which to keep.",
                ))
        for m in members:
            results[m.line_id].related_line_ids = [i for i in ids if i != m.line_id]

    return [results[ln.line_id] for ln in lines]
