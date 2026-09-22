import { formatUsd } from "./money";
import type { Change, OfferLine } from "./types";

/**
 * What a line needs before including it is worth sending.
 *
 * The server is still the validator: this checks presence only, and a value
 * that is present but wrong ("abc", 0.5 pieces) goes to the server and comes
 * back in its own words. What this catches is the include that could only be
 * refused — a blank cost, or the sheet's own value for a field the server has
 * already flagged (a -12 that nobody changed). Asking for it at the line is
 * better than failing at save.
 */

export type RequiredField = "item_code" | "size" | "quantity" | "unit_cost";
export type FieldValues = Record<RequiredField, string>;

export const REQUIRED_FIELDS: RequiredField[] = ["item_code", "size", "quantity", "unit_cost"];

export const FIELD_NAME: Record<RequiredField, string> = {
  item_code: "item code",
  size: "size",
  quantity: "number of pieces",
  unit_cost: "supplier cost",
};

// overridden_fields uses the stored column names; money is stored in units.
const STORED_AS: Record<RequiredField, string> = {
  item_code: "item_code",
  size: "size",
  quantity: "quantity",
  unit_cost: "unit_cost_units",
};

export interface Gap {
  field: RequiredField;
  /** The sheet's value when it is there but flagged, e.g. "-12". */
  unusable: string | null;
}

/** The line's effective values, as the editor's inputs start out. */
export function currentValues(line: OfferLine): FieldValues {
  return {
    item_code: line.item_code ?? "",
    size: line.size ?? "",
    quantity: line.quantity?.toString() ?? "",
    unit_cost: line.unit_cost ?? "",
  };
}

export function gapsFor(line: OfferLine, values: FieldValues): Gap[] {
  const current = currentValues(line);
  const gaps: Gap[] = [];
  for (const field of REQUIRED_FIELDS) {
    const value = values[field].trim();
    if (value === "") {
      gaps.push({ field, unusable: null });
      continue;
    }
    // The server said this field is the problem, the value still came from
    // the sheet, and nobody has changed it: including it as-is cannot pass.
    const flagged = line.issues.some(
      (issue) => issue.kind === "needs_decision" && issue.field === field,
    );
    const fromSheet = !line.overridden_fields.includes(STORED_AS[field]);
    if (flagged && fromSheet && value === current[field]) {
      gaps.push({ field, unusable: field === "unit_cost" ? formatUsd(value) : value });
    }
  }
  return gaps;
}

/** "the supplier cost", "the number of pieces (the sheet's -12 can't be used)" */
export function describeGaps(gaps: Gap[]): string {
  const parts = gaps.map((gap) =>
    gap.unusable === null
      ? `the ${FIELD_NAME[gap.field]}`
      : `the ${FIELD_NAME[gap.field]} (the sheet's ${gap.unusable} can't be used)`,
  );
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/**
 * An include carrying only the fields that differ from the line's effective
 * values, so the original parse stays visible and a reset restores it.
 */
export function includeChange(line: OfferLine, values: FieldValues, note = ""): Change {
  const current = currentValues(line);
  const change: Change = { line_id: line.line_id, action: "include" };
  const itemCode = values.item_code.trim();
  const size = values.size.trim();
  const quantity = values.quantity.trim();
  const unitCost = values.unit_cost.trim();
  if (itemCode !== current.item_code) change.item_code = itemCode;
  if (size !== current.size) change.size = size;
  if (quantity !== current.quantity) {
    // Sent only when it is a whole number; anything else goes as-is so the
    // server can explain what is wrong with it in its own words.
    const whole = /^-?\d+$/.test(quantity);
    change.quantity = whole
      ? Number.parseInt(quantity, 10)
      : (quantity as unknown as number);
  }
  if (unitCost !== current.unit_cost) change.unit_cost = unitCost;
  if (note.trim()) change.note = note.trim();
  return change;
}
