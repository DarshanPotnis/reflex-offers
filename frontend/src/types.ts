/**
 * Mirrors the serializers in backend/app/api.py.
 *
 * Every amount is a decimal string, never a number. The backend stores money
 * as integer ten-thousandths of a dollar precisely so it never becomes a
 * float; parsing one here would undo that.
 */

export type IssueKind = "fixed" | "warning" | "auto_excluded" | "needs_decision";
export type ParseStatus = "ok" | "normalized" | "missing" | "invalid";
export type LineStatus = "included" | "excluded";
export type ChangeAction = "include" | "exclude" | "reset";

export interface Issue {
  code: string;
  kind: IssueKind;
  message: string;
  field: string | null;
}

export interface SourceCell {
  coordinate: string;
  raw: string | number | boolean | null;
}

export interface ParsedValue {
  value: string | number | null;
  status: ParseStatus;
  note: string | null;
}

export interface LineDecision {
  action: "include" | "exclude";
  item_code: string | null;
  size: string | null;
  quantity: number | null;
  unit_cost: string | null;
  note: string | null;
}

export interface OfferLine {
  line_id: string;
  position: number;
  source_row: number;
  item_code: string | null;
  description: string | null;
  size: string | null;
  category: string | null;
  quantity: number | null;
  unit_cost: string | null;
  retail: string | null;
  line_value: string | null;
  original: Record<string, ParsedValue>;
  cells: Record<string, SourceCell>;
  issues: Issue[];
  related_line_ids: string[];
  status: LineStatus;
  status_reason: string;
  needs_decision_open: boolean;
  overridden_fields: string[];
  decision: LineDecision | null;
}

export interface Summary {
  total_lines: number;
  included_lines: number;
  pieces: number;
  supplier_cost: string;
  retail_reference: string;
  needs_decision_open: number;
  warnings: number;
  fixed: number;
  auto_excluded: number;
  user_excluded: number;
}

export interface Offer {
  id: string;
  version: number;
  supplier_name: string | null;
  title: string | null;
  layout: string;
  sheet_name: string | null;
  source_filename: string | null;
  source_sha256: string;
  created_at: string;
  updated_at: string;
  notices: string[];
  summary: Summary;
  lines: OfferLine[];
}

export interface OfferListing {
  id: string;
  supplier_name: string | null;
  source_filename: string | null;
  created_at: string;
  version: number;
}

/** One requested edit. Sent verbatim; the server is the validator. */
export interface Change {
  line_id: string;
  action: ChangeAction;
  item_code?: string;
  size?: string;
  quantity?: number;
  unit_cost?: string;
  note?: string;
}

export interface Receipt {
  offer_id: string;
  request_id: string;
  version: number;
  applied: number;
}

export interface LineErrors {
  line_id: string | null;
  messages: string[];
}

export interface Config {
  fault_injection: boolean;
}
