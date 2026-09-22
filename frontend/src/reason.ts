import type { OfferLine } from "./types";

/**
 * A few words saying why a line is out of the totals, short enough to sit in
 * the list without anyone expanding a row.
 *
 * The brief asks us to "make clear what was left out and why", and a reason
 * you have to click to read does not do that. The wording here is
 * presentation; every value in it — the quantity, the raw cell, the row
 * numbers — comes from the API.
 */

function rawCell(line: OfferLine, field: string): string {
  const cell = line.cells[field];
  if (!cell || cell.raw === null || cell.raw === undefined) return "";
  return String(cell.raw).trim();
}

function siblingRows(line: OfferLine, linesById: Map<string, OfferLine>): number[] {
  return line.related_line_ids
    .map((id) => linesById.get(id)?.source_row)
    .filter((row): row is number => typeof row === "number")
    .sort((a, b) => a - b);
}

function rowList(rows: number[]): string {
  if (rows.length === 0) return "";
  if (rows.length === 1) return `row ${rows[0]}`;
  return `rows ${rows.slice(0, -1).join(", ")} and ${rows[rows.length - 1]}`;
}

export function lineReason(
  line: OfferLine,
  linesById: Map<string, OfferLine>,
): string | null {
  if (line.status === "included" && !line.needs_decision_open) return null;

  if (line.decision?.action === "exclude") {
    return line.decision.note
      ? `You excluded this — ${line.decision.note}`
      : "You excluded this";
  }

  const codes = new Set(line.issues.map((issue) => issue.code));

  // Ordered so a value problem is named before a grouping one: you have to
  // fix the number before the duplicate is even meaningful.
  if (codes.has("MISSING_ITEM_CODE")) return "No item code";
  if (codes.has("INVALID_ITEM_CODE")) return "Item code isn't usable";
  if (codes.has("MISSING_SIZE")) return "No size";
  if (codes.has("MISSING_QUANTITY")) return "No quantity";
  if (codes.has("INVALID_QUANTITY")) {
    const raw = rawCell(line, "quantity");
    return raw ? `Quantity '${raw}' isn't a number` : "Quantity isn't a number";
  }
  if (codes.has("NEGATIVE_QUANTITY")) {
    return `Quantity is ${line.original.quantity?.value ?? line.quantity}`;
  }
  if (codes.has("ZERO_QUANTITY")) return "0 pieces";
  if (codes.has("MISSING_COST")) return "No supplier cost";
  if (codes.has("INVALID_COST")) {
    const raw = rawCell(line, "unit_cost");
    return raw ? `Supplier cost '${raw}' isn't a number` : "Supplier cost isn't a number";
  }
  if (codes.has("NONPOSITIVE_COST")) return "Supplier cost must be above $0";
  if (codes.has("DUPLICATE_ROW")) {
    const rows = siblingRows(line, linesById);
    return rows.length ? `Duplicate of ${rowList(rows)}` : "Duplicate row";
  }
  if (codes.has("CONFLICTING_ROWS")) {
    const rows = siblingRows(line, linesById);
    return rows.length ? `Conflicts with ${rowList(rows)}` : "Conflicting values";
  }

  // Anything unmapped still says something true rather than nothing.
  const blocking = line.issues.find(
    (issue) => issue.kind === "needs_decision" || issue.kind === "auto_excluded",
  );
  return blocking?.message ?? null;
}
