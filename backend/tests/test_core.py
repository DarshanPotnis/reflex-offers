"""Answer-key tests for the parsing engine.

These numbers were verified by hand, cell by cell, against the supplied
files. If a change breaks one of them, the change is wrong until proven
otherwise.
"""

from pathlib import Path

import pytest

from app.core.money import format_amount, format_usd, to_units
from app.core.normalize import parse_item_code, parse_money, parse_quantity, parse_text
from app.core.reader import SourceCell, SourceLine, UnsupportedWorkbook, read_workbook
from app.core.rules import analyze

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    result = read_workbook((FIXTURES / name).read_bytes())
    return result, analyze(result.lines)


def included_totals(analyses):
    pieces = cost = retail = 0
    lines = 0
    for a in analyses:
        if a.default_included:
            ln = a.line
            lines += 1
            pieces += ln.quantity.value
            cost += ln.quantity.value * to_units(ln.unit_cost.value)
            if ln.retail.usable and ln.retail.value is not None and ln.retail.value > 0:
                retail += ln.quantity.value * to_units(ln.retail.value)
    return lines, pieces, cost, retail


def by_id(analyses):
    return {a.line.line_id: a for a in analyses}


def codes(analysis):
    return {i.code for i in analysis.issues}


# ---------- answer key ----------

@pytest.mark.parametrize("name, layout, lines, pieces, cost, retail", [
    ("01-northstar-line-sheet.xlsx", "northstar", 6, 1_733, "4208.00", "21552.00"),
    ("02-harbor-size-grid.xlsx", "harbor", 22, 1_340, "3740.00", "16144.00"),
    ("03-northstar-5000-rows.xlsx", "northstar", 5_000, 62_444, "187214.50", "1031508.00"),
])
def test_answer_key_totals(name, layout, lines, pieces, cost, retail):
    result, analyses = load(name)
    assert result.layout == layout
    got_lines, got_pieces, got_cost, got_retail = included_totals(analyses)
    assert got_lines == lines
    assert got_pieces == pieces
    assert format_amount(got_cost) == cost
    assert format_amount(got_retail) == retail


# ---------- Northstar traps ----------

def test_northstar_every_trap_classified():
    _, analyses = load("01-northstar-line-sheet.xlsx")
    a = by_id(analyses)

    assert a["R6"].line.item_code.value == "000101"          # leading zeros kept
    assert "DUPLICATE_KEPT" in codes(a["R6"]) and a["R6"].default_included
    assert "DUPLICATE_ROW" in codes(a["R10"]) and not a["R10"].default_included
    assert a["R7"].line.unit_cost.value == parse_money("3.25").value  # "$3.25"
    assert "MISSING_COST" in codes(a["R9"])
    assert "NEGATIVE_QUANTITY" in codes(a["R12"])             # -12 never flipped
    assert "ZERO_QUANTITY" in codes(a["R13"]) and not a["R13"].needs_decision
    assert "CONFLICTING_ROWS" in codes(a["R14"]) and "CONFLICTING_ROWS" in codes(a["R15"])
    assert not a["R14"].default_included and not a["R15"].default_included
    assert "MISSING_ITEM_CODE" in codes(a["R16"])             # no invented code
    assert a["R17"].line.quantity.value == 1200               # "1,200"
    assert "INVALID_QUANTITY" in codes(a["R18"])              # "TBD"
    assert a["R19"].line.quantity.value == 45                 # " 45 "


def test_footer_is_reported_not_parsed():
    result, _ = load("01-northstar-line-sheet.xlsx")
    assert all(ln.source_row < 21 for ln in result.lines)
    assert any("Row 21 skipped" in n for n in result.notices)


# ---------- Harbor traps ----------

def test_harbor_one_line_per_size_and_traps():
    _, analyses = load("02-harbor-size-grid.xlsx")
    a = by_id(analyses)

    assert a["R9-S"].default_included and a["R9-L"].default_included
    assert "INVALID_QUANTITY" in codes(a["R9-M"])             # only M is TBD
    assert a["R10-S"].line.item_code.value == "000101"
    assert all("ZERO_QUANTITY" in codes(a[f"R11-{s}"]) for s in "SML")
    assert all("MISSING_COST" in codes(a[f"R13-{s}"]) for s in "SML")
    assert "NEGATIVE_QUANTITY" in codes(a["R15-M"])
    assert a["R15-S"].default_included and a["R15-L"].default_included
    assert "SUPPLIER_TOTAL_MISMATCH" in codes(a["R15-S"])     # supplier said 21, usable 24


def test_harbor_supplier_total_is_never_used_as_a_quantity():
    result, _ = load("02-harbor-size-grid.xlsx")
    assert all(ln.size.value in {"S", "M", "L"} for ln in result.lines)


# ---------- 5,000-row file ----------

def test_volume_file_is_clean():
    _, analyses = load("03-northstar-5000-rows.xlsx")
    assert len(analyses) == 5_000
    assert not any(a.needs_decision for a in analyses)


# ---------- unit rules ----------

def test_leading_zeros_restored_from_number_format():
    parsed = parse_item_code(101, "000000")
    assert parsed.value == "000101" and parsed.status == "normalized"


@pytest.mark.parametrize("raw, expected", [
    (120, 120), (" 45 ", 45), ("1,200", 1200), (12.0, 12), ("7.00", 7),
])
def test_quantity_accepts_clear_values(raw, expected):
    assert parse_quantity(raw).value == expected


@pytest.mark.parametrize("raw", ["TBD", 12.5, "12.5", "1.200", "twelve", "1,20"])
def test_quantity_refuses_to_guess(raw):
    assert parse_quantity(raw).status == "invalid"


@pytest.mark.parametrize("raw, expected", [
    ("$3.25", "3.25"), ("3.25 USD", "3.25"), ("1,200.50", "1200.50"), (1.8, "1.80"), (4, "4.00"),
])
def test_money_accepts_clear_values(raw, expected):
    assert format_amount(to_units(parse_money(raw).value)) == expected


@pytest.mark.parametrize("raw", ["TBD", "3 dollars", "1.23456", "€3"])
def test_money_refuses_to_guess(raw):
    assert parse_money(raw).status == "invalid"


def test_money_is_exact_across_many_lines():
    # 240 * 1.8 as floats accumulates error; integer units never do.
    assert format_usd(sum(240 * to_units(parse_money(1.8).value) for _ in range(5_000))) == "$2,160,000.00"


def test_unsupported_file_gets_a_clear_error():
    with pytest.raises(UnsupportedWorkbook, match="Excel workbook"):
        read_workbook(b"not a spreadsheet")


# ---------- duplicates vs conflicts ----------

def make_line(line_id, row, code, size, qty, cost, retail="10.00",
              description="Tee", category=None):
    """A SourceLine built by hand, so a grouping rule can be tested on its own."""
    fields = {"item_code": code, "description": description, "size": size,
              "category": category, "quantity": qty, "unit_cost": cost, "retail": retail}
    return SourceLine(
        line_id=line_id, source_row=row,
        item_code=parse_text(code), description=parse_text(description),
        size=parse_text(size), category=parse_text(category),
        quantity=parse_quantity(qty), unit_cost=parse_money(cost),
        retail=parse_money(retail),
        cells={n: SourceCell(f"A{row}", v) for n, v in fields.items()},
    )


def test_differing_text_is_a_duplicate_not_a_conflict():
    """A different description changes no number, so it cannot promote a
    duplicate to a conflict. It is named in the message instead."""
    a = by_id(analyze([
        make_line("R2", 2, "A100", "M", 10, "3.00", description="Cotton Tee"),
        make_line("R3", 3, "A100", "M", 10, "3.00", description="Cotton T-Shirt"),
    ]))
    assert "DUPLICATE_KEPT" in codes(a["R2"]) and a["R2"].default_included
    assert "DUPLICATE_ROW" in codes(a["R3"]) and not a["R3"].default_included
    kept = next(i for i in a["R2"].issues if i.code == "DUPLICATE_KEPT")
    assert "Cotton Tee vs Cotton T-Shirt" in kept.message


def test_differing_numbers_are_a_conflict():
    a = by_id(analyze([
        make_line("R2", 2, "A100", "M", 10, "3.00"),
        make_line("R3", 3, "A100", "M", 10, "3.50"),
    ]))
    for line_id in ("R2", "R3"):
        assert "CONFLICTING_ROWS" in codes(a[line_id])
        assert not a[line_id].default_included
    message = next(i for i in a["R2"].issues if i.code == "CONFLICTING_ROWS").message
    assert "cost $3.00 vs $3.50" in message


def test_an_exact_duplicate_still_reads_as_identical():
    a = by_id(analyze([
        make_line("R2", 2, "A100", "M", 10, "3.00"),
        make_line("R3", 3, "A100", "M", 10, "3.00"),
    ]))
    kept = next(i for i in a["R2"].issues if i.code == "DUPLICATE_KEPT")
    assert kept.message == "Identical to row(s) 3. Counted once here."
