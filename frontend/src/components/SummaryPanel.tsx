import { formatCount, formatUsd, plural } from "../money";
import type { Summary } from "../types";

/**
 * The two totals are deliberately not side-by-side equals. What we pay the
 * supplier is the decision; retail is a reference that must never be read as
 * money we hand over.
 */
export function SummaryPanel({ summary }: { summary: Summary }) {
  return (
    <div className="totals">
      <div className="card total-cost">
        <div className="total-label">What we'd pay the supplier</div>
        <div className="total-figure num">{formatUsd(summary.supplier_cost)}</div>
        <div className="total-sub num">
          {formatCount(summary.included_lines)}{" "}
          {plural(summary.included_lines, "line")} ·{" "}
          {formatCount(summary.pieces)} {plural(summary.pieces, "piece")}{" "}
          available
        </div>
      </div>
      <div className="card total-retail">
        <div className="total-label">Retail reference</div>
        <div className="total-figure num muted">
          {formatUsd(summary.retail_reference)}
        </div>
        <div className="total-sub">
          <strong>Not what we pay.</strong> Never part of the supplier total.
        </div>
      </div>
    </div>
  );
}
