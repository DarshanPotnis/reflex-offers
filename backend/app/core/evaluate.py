"""Apply a person's decisions to the stored lines and total the result.

This is the single place that decides what an offer is worth. The API, the
screen and the CSV all call it, so "the download agrees with the screen" is
structural rather than something anyone has to remember.

Three ideas hold it together:

``StoredLine``
    Storage-neutral. Exactly the fields ``offer_lines`` holds, money already
    in int units. Upload adapts ``Analysis`` into it once (``store``); every
    later GET and export builds it from the database. The workbook is never
    re-read, and both paths run identical logic.

``prepare_change``
    The gate. It takes a change exactly as it arrived from JSON, says in
    plain English everything wrong with it, and — only if nothing is wrong —
    returns the ``Decision`` to store. Override fields are typed ``Any`` on
    purpose: this layer has to be able to reject a float quantity or a
    numeric cost even when the API layer let one through.

``evaluate``
    Decisions over lines -> effective lines + summary. It never mutates a
    stored line; the original parse stays visible beside the effective value
    so a reviewer can always check it against the sheet.

Default policy is unchanged here: a line the supplier left questionable stays
out of the totals until a person says otherwise.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from .money import UNITS_PER_DOLLAR, MoneyPrecisionError, format_amount, to_units
from .normalize import Parsed, parse_money
from .rules import Analysis, Issue

Action = Literal["include", "exclude"]
ChangeAction = Literal["include", "exclude", "reset"]
LineStatus = Literal["included", "excluded"]

MAX_TEXT = 64
MAX_QUANTITY = 10_000_000
MAX_COST_DOLLARS = 1_000_000
MAX_COST_UNITS = MAX_COST_DOLLARS * UNITS_PER_DOLLAR
MAX_NOTE = 500

_FIELD_WORDS = {
    "item_code": "Item code",
    "size": "Size",
    "quantity": "Pieces",
    "unit_cost": "Supplier cost",
}


# --------------------------------------------------------------------------
# What storage holds
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StoredCell:
    """One original supplier cell, kept so a reviewer can check the sheet."""

    coordinate: str
    raw: Any


@dataclass(frozen=True)
class StoredLine:
    """A parsed line as ``offer_lines`` holds it. Written once, never edited.

    Money fields are ``Parsed[int]`` in units, not ``Decimal`` — the
    conversion happens once at upload so nothing downstream can reintroduce
    a float.
    """

    line_id: str
    position: int
    source_row: int
    item_code: Parsed[str]
    description: Parsed[str]
    size: Parsed[str]
    category: Parsed[str]
    quantity: Parsed[int]
    unit_cost_units: Parsed[int]
    retail_units: Parsed[int]
    cells: dict[str, StoredCell]
    issues: tuple[Issue, ...]
    related_line_ids: tuple[str, ...]
    default_included: bool


def _units(parsed: Parsed[Decimal]) -> Parsed[int]:
    if parsed.value is None:
        return Parsed(None, parsed.status, parsed.note)
    return Parsed(to_units(parsed.value), parsed.status, parsed.note)


def from_analysis(analysis: Analysis, position: int) -> StoredLine:
    line = analysis.line
    return StoredLine(
        line_id=line.line_id,
        position=position,
        source_row=line.source_row,
        item_code=line.item_code,
        description=line.description,
        size=line.size,
        category=line.category,
        quantity=line.quantity,
        unit_cost_units=_units(line.unit_cost),
        retail_units=_units(line.retail),
        cells={name: StoredCell(c.coordinate, c.raw) for name, c in line.cells.items()},
        issues=tuple(analysis.issues),
        related_line_ids=tuple(analysis.related_line_ids),
        default_included=analysis.default_included,
    )


def store(analyses: list[Analysis]) -> list[StoredLine]:
    """Adapt a freshly analysed workbook into what we persist."""
    return [from_analysis(a, i) for i, a in enumerate(analyses)]


# --------------------------------------------------------------------------
# What a person decided
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """A saved decision. Only the fields the user actually overrode are set,
    so the original parse stays visible and a reset is a clean delete."""

    line_id: str
    action: Action
    item_code: str | None = None
    size: str | None = None
    quantity: int | None = None
    unit_cost_units: int | None = None
    note: str | None = None


@dataclass(frozen=True)
class Change:
    """One requested edit, exactly as it arrived from JSON.

    Override fields are ``Any`` deliberately — see the module docstring.
    """

    line_id: str
    action: Any
    item_code: Any = None
    size: Any = None
    quantity: Any = None
    unit_cost: Any = None  # decimal string, e.g. "4.50"
    note: Any = None


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _type_word(value: Any) -> str:
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return f"true/false ({str(value).lower()})"
    if isinstance(value, float):
        return f"a decimal number ({value!r})"
    if isinstance(value, int):
        return f"a whole number ({value!r})"
    if isinstance(value, str):
        return f"text ({value!r})"
    return repr(value)


def _text_override(raw: Any, label: str) -> tuple[str | None, list[str]]:
    if raw is None:
        return None, []
    if not isinstance(raw, str):
        return None, [f"{label} must be text, not {_type_word(raw)}."]
    text = raw.strip()
    if not text:
        return None, [f"{label} can't be blank."]
    if len(text) > MAX_TEXT:
        return None, [f"{label} is {len(text)} characters; the most we store is {MAX_TEXT}."]
    return text, []


def _quantity_override(raw: Any) -> tuple[int | None, list[str]]:
    """Pieces must arrive as a JSON integer.

    ``"45"`` and ``45.0`` are refused on purpose. They are what a buggy
    client sends, not what a supplier typed, and accepting them would mean
    guessing which one the sender meant.
    """
    if raw is None:
        return None, []
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, [
            f"Pieces must be a whole number, not {_type_word(raw)}. Send 45, not \"45\" or 45.0."
        ]
    return raw, []


def _cost_override(raw: Any) -> tuple[int | None, list[str]]:
    if raw is None:
        return None, []
    if not isinstance(raw, str):
        return None, [
            f"Supplier cost must be a decimal string like \"4.50\", not {_type_word(raw)}."
        ]
    parsed = parse_money(raw)
    if not parsed.usable or parsed.value is None:
        # parse_money's note for unreadable text just restates the problem;
        # say what to type instead. Its precision note does add something.
        note = parsed.note or ""
        detail = f"{note}." if "decimal places" in note else 'Use a decimal like "4.50".'
        return None, [f"Supplier cost {raw!r} isn't an amount we can read. {detail}"]
    try:
        return to_units(parsed.value), []
    except MoneyPrecisionError as exc:  # parse_money already guards this
        return None, [f"Supplier cost {raw!r} is too precise: {exc}"]


def _note_override(raw: Any) -> tuple[str | None, list[str]]:
    if raw is None:
        return None, []
    if not isinstance(raw, str):
        return None, [f"Note must be text, not {_type_word(raw)}."]
    text = raw.strip()
    if not text:
        return None, []
    if len(text) > MAX_NOTE:
        return None, [f"Note is {len(text)} characters; the most we store is {MAX_NOTE}."]
    return text, []


def _parsed_or_none(parsed: Parsed[Any]) -> Any:
    return parsed.value if parsed.usable else None


def prepare_change(line: StoredLine | None, change: Change) -> tuple[Decision | None, list[str]]:
    """Check one change and, if it is sound, build the decision to store.

    Returns ``(decision, errors)``. ``decision`` is ``None`` for a reset and
    whenever there are errors. Errors are plain English and name no line id —
    the API groups them by line.
    """
    if line is None:
        return None, [f"There is no line {change.line_id!r} in this offer."]

    action = change.action
    if action not in ("include", "exclude", "reset"):
        return None, [f"Action must be include, exclude or reset, not {_type_word(action)}."]

    errors: list[str] = []
    given = [n for n in ("item_code", "size", "quantity", "unit_cost")
             if getattr(change, n) is not None]

    if action == "reset":
        if given or change.note is not None:
            errors.append(
                "A reset clears the whole decision, so it can't carry values or a note."
            )
        return None, errors

    note, note_errors = _note_override(change.note)
    errors.extend(note_errors)

    if action == "exclude":
        if given:
            errors.append(
                f"{', '.join(_FIELD_WORDS[n] for n in given)} can't be set while excluding a "
                "line — the values would never be checked. Use include to change a value."
            )
        if errors:
            return None, errors
        return Decision(change.line_id, "exclude", note=note), []

    code, code_errors = _text_override(change.item_code, "Item code")
    size, size_errors = _text_override(change.size, "Size")
    quantity, quantity_errors = _quantity_override(change.quantity)
    cost, cost_errors = _cost_override(change.unit_cost)
    errors.extend(code_errors + size_errors + quantity_errors + cost_errors)

    # Check the effective value only where the override itself was sound,
    # so one bad field never produces two confusing messages.
    if not code_errors:
        if (code if code is not None else _parsed_or_none(line.item_code)) is None:
            errors.append("This line has no item code. Enter one to include it.")

    if not size_errors:
        if (size if size is not None else _parsed_or_none(line.size)) is None:
            errors.append("This line has no size. Enter one to include it.")

    if not quantity_errors:
        effective = quantity if quantity is not None else _parsed_or_none(line.quantity)
        if effective is None:
            errors.append("This line has no usable number of pieces. Enter one to include it.")
        elif not 1 <= effective <= MAX_QUANTITY:
            errors.append(
                f"Pieces must be between 1 and {MAX_QUANTITY:,}; this is {effective:,}."
            )

    if not cost_errors:
        effective = cost if cost is not None else _parsed_or_none(line.unit_cost_units)
        if effective is None:
            errors.append("This line has no usable supplier cost. Enter one to include it.")
        elif effective <= 0:
            errors.append(
                f"Supplier cost must be above $0; this is {format_amount(effective)}."
            )
        elif effective > MAX_COST_UNITS:
            errors.append(
                f"Supplier cost {format_amount(effective)} is above the "
                f"{MAX_COST_DOLLARS:,} limit."
            )

    if errors:
        return None, errors
    return Decision(
        line_id=change.line_id,
        action="include",
        item_code=code,
        size=size,
        quantity=quantity,
        unit_cost_units=cost,
        note=note,
    ), []


def validate_change(line: StoredLine | None, change: Change) -> list[str]:
    """Everything wrong with this change, in plain English. Empty means fine."""
    return prepare_change(line, change)[1]


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EvaluatedLine:
    line_id: str
    position: int
    source_row: int
    item_code: str | None
    description: str | None
    size: str | None
    category: str | None
    quantity: int | None
    unit_cost_units: int | None
    retail_units: int | None
    line_value_units: int | None
    # What the line would add if it were included; set whether or not it is.
    # Never summed — only ``line_value_units`` reaches a total.
    would_be_value_units: int | None
    original: StoredLine
    cells: dict[str, StoredCell]
    issues: tuple[Issue, ...]
    related_line_ids: tuple[str, ...]
    status: LineStatus
    status_reason: str
    decision: Decision | None
    needs_decision_open: bool
    overridden_fields: tuple[str, ...]

    @property
    def included(self) -> bool:
        return self.status == "included"


@dataclass(frozen=True)
class Summary:
    """Counts are of *lines*, so each one can label a tab on the screen."""

    total_lines: int
    included_lines: int
    pieces: int
    supplier_cost_units: int
    retail_reference_units: int
    needs_decision_open: int
    warnings: int
    fixed: int
    auto_excluded: int
    user_excluded: int


@dataclass(frozen=True)
class EvaluatedOffer:
    lines: tuple[EvaluatedLine, ...]
    summary: Summary

    def by_id(self) -> dict[str, EvaluatedLine]:
        return {line.line_id: line for line in self.lines}


def _blocking_reason(line: StoredLine) -> str:
    for kind in ("needs_decision", "auto_excluded"):
        for issue in line.issues:
            if issue.kind == kind:
                return issue.message
    return "Left out by default."


def _override_words(decision: Decision) -> list[str]:
    words = []
    if decision.item_code is not None:
        words.append(f"item code {decision.item_code}")
    if decision.size is not None:
        words.append(f"size {decision.size}")
    if decision.quantity is not None:
        words.append(f"pieces {decision.quantity:,}")
    if decision.unit_cost_units is not None:
        words.append(f"cost {format_amount(decision.unit_cost_units)}")
    return words


def evaluate(lines: list[StoredLine], decisions: list[Decision]) -> EvaluatedOffer:
    """Lines plus decisions -> effective lines and the totals that follow."""
    by_line = {d.line_id: d for d in decisions}
    working: list[dict[str, Any]] = []

    for line in lines:
        decision = by_line.get(line.line_id)
        overridden: list[str] = []

        def pick(field: str, parsed: Parsed[Any]) -> Any:
            override = getattr(decision, field, None) if decision else None
            if override is not None:
                overridden.append(field)
                return override
            return _parsed_or_none(parsed)

        item_code = pick("item_code", line.item_code)
        size = pick("size", line.size)
        quantity = pick("quantity", line.quantity)
        unit_cost_units = pick("unit_cost_units", line.unit_cost_units)
        retail_units = _parsed_or_none(line.retail_units)

        complete = (
            item_code is not None and size is not None
            and quantity is not None and quantity > 0
            and unit_cost_units is not None and unit_cost_units > 0
        )

        if decision is not None and decision.action == "exclude":
            status: LineStatus = "excluded"
            reason = "You excluded this line."
            if decision.note:
                reason = f"{reason} {decision.note}"
        elif decision is not None and decision.action == "include":
            if complete:
                status = "included"
                words = _override_words(decision)
                reason = (
                    f"You included this line, setting {', '.join(words)}."
                    if words else "You included this line."
                )
            else:
                # Only reachable if a decision was written around the API.
                status = "excluded"
                reason = (
                    "A saved decision includes this line, but its values are incomplete, "
                    "so it is left out of the totals."
                )
        elif line.default_included:
            status = "included"
            reason = (
                "Included by default, with a warning worth checking."
                if any(i.kind == "warning" for i in line.issues)
                else "Included by default."
            )
        else:
            status = "excluded"
            reason = _blocking_reason(line)

        working.append({
            "line": line,
            "decision": decision,
            "item_code": item_code,
            "size": size,
            "quantity": quantity,
            "unit_cost_units": unit_cost_units,
            "retail_units": retail_units,
            "status": status,
            "status_reason": reason,
            "issues": list(line.issues),
            # Overrides are recorded in the order picked above; sort for a
            # stable payload.
            "overridden": tuple(sorted(overridden)),
        })

    _flag_duplicates_after_edit(working)

    evaluated = tuple(
        EvaluatedLine(
            line_id=w["line"].line_id,
            position=w["line"].position,
            source_row=w["line"].source_row,
            item_code=w["item_code"],
            description=_parsed_or_none(w["line"].description),
            size=w["size"],
            category=_parsed_or_none(w["line"].category),
            quantity=w["quantity"],
            unit_cost_units=w["unit_cost_units"],
            retail_units=w["retail_units"],
            line_value_units=(
                w["quantity"] * w["unit_cost_units"] if w["status"] == "included" else None
            ),
            would_be_value_units=_would_be_value(w["quantity"], w["unit_cost_units"]),
            original=w["line"],
            cells=w["line"].cells,
            issues=tuple(w["issues"]),
            related_line_ids=w["line"].related_line_ids,
            status=w["status"],
            status_reason=w["status_reason"],
            decision=w["decision"],
            needs_decision_open=(
                w["decision"] is None
                and any(i.kind == "needs_decision" for i in w["line"].issues)
            ),
            overridden_fields=w["overridden"],
        )
        for w in working
    )
    return EvaluatedOffer(evaluated, _summarise(evaluated))


def _would_be_value(quantity: int | None, unit_cost_units: int | None) -> int | None:
    """Pieces x cost for a line that has both, included or not.

    A card comparing two conflicting rows has to show each row's value, and
    both rows are excluded until someone picks one. The browser is not allowed
    to multiply money, so the server sends it. Nothing is invented: a line
    without a positive quantity and cost gets no value at all.
    """
    if quantity is None or unit_cost_units is None:
        return None
    if quantity <= 0 or unit_cost_units <= 0:
        return None
    return quantity * unit_cost_units


def _flag_duplicates_after_edit(working: list[dict[str, Any]]) -> None:
    """Warn when edits leave two *included* lines on the same item and size.

    Upload-time grouping is frozen in ``offer_lines.issues``, so without this
    an override that collides with another line — or including both halves of
    a conflict — would overstate stock in silence. A warning, never a block:
    two genuine lots of the same item are possible.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for w in working:
        if w["status"] == "included" and w["item_code"] and w["size"]:
            groups[(w["item_code"].casefold(), w["size"].casefold())].append(w)

    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m["line"].source_row)
        for w in members:
            others = ", ".join(
                str(o["line"].source_row) for o in members if o is not w
            )
            w["issues"].append(Issue(
                "DUPLICATE_AFTER_EDIT", "warning",
                f"{w['item_code']} ({w['size']}) is also included on row(s) {others}. "
                "Two included lines with the same item and size may count the same "
                "stock twice.",
            ))


def _summarise(lines: tuple[EvaluatedLine, ...]) -> Summary:
    pieces = cost = retail = 0
    included = needs_decision = warnings = fixed = auto_excluded = user_excluded = 0

    for line in lines:
        kinds = {i.kind for i in line.issues}
        if "warning" in kinds:
            warnings += 1
        if "fixed" in kinds:
            fixed += 1
        if line.needs_decision_open:
            needs_decision += 1

        if line.status == "included":
            included += 1
            pieces += line.quantity or 0
            cost += line.line_value_units or 0
            if line.retail_units is not None and line.retail_units > 0:
                retail += (line.quantity or 0) * line.retail_units
        elif line.decision is not None:
            user_excluded += 1
        elif "auto_excluded" in kinds:
            auto_excluded += 1

    return Summary(
        total_lines=len(lines),
        included_lines=included,
        pieces=pieces,
        supplier_cost_units=cost,
        retail_reference_units=retail,
        needs_decision_open=needs_decision,
        warnings=warnings,
        fixed=fixed,
        auto_excluded=auto_excluded,
        user_excluded=user_excluded,
    )
