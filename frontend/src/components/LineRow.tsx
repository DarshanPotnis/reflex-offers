import { formatCount, formatUsd } from "../money";
import { lineReason } from "../reason";
import type { Change, OfferLine } from "../types";
import { LineDetail } from "./LineDetail";

export function LineTableHeader() {
  return (
    <div className="line-header" aria-hidden="true">
      <span />
      <span>Item</span>
      <span>Description</span>
      <span className="hide-narrow">Size</span>
      <span className="hide-narrow right">Pieces</span>
      <span className="hide-narrow right">Supplier cost</span>
      <span className="hide-narrow right">Line value</span>
      <span>Status</span>
    </div>
  );
}

export function LineRow({
  line,
  expanded,
  staged,
  errors,
  linesById,
  onToggle,
  onStage,
  onUnstage,
}: {
  line: OfferLine;
  expanded: boolean;
  staged: Change | undefined;
  errors: string[];
  linesById: Map<string, OfferLine>;
  onToggle: () => void;
  onStage: (change: Change) => void;
  onUnstage: () => void;
}) {
  // Why a line is out of the totals, readable without expanding anything.
  const reason = lineReason(line, linesById);
  const autoExcluded =
    !line.needs_decision_open &&
    line.issues.some((issue) => issue.kind === "auto_excluded");

  const classes = [
    "line",
    line.status === "excluded" ? "is-excluded" : "",
    autoExcluded ? "is-auto-excluded" : "",
    staged ? "is-staged" : "",
    errors.length > 0 ? "has-error" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      <button
        className="line-head"
        onClick={onToggle}
        aria-expanded={expanded}
        title={line.status_reason}
      >
        <span className="caret">{expanded ? "▾" : "▸"}</span>
        <span className="line-code">
          {line.item_code ?? <em className="muted">no code</em>}
        </span>
        <span>
          <span className="line-desc">{line.description}</span>
          {reason && <span className="line-reason">{reason}</span>}
        </span>
        <span className="hide-narrow">{line.size}</span>
        <span className="num hide-narrow right">
          {line.quantity === null ? "—" : formatCount(line.quantity)}
        </span>
        <span className="num hide-narrow right">{formatUsd(line.unit_cost)}</span>
        <span className="num hide-narrow right">
          <strong>{formatUsd(line.line_value)}</strong>
        </span>
        <span>
          {staged ? (
            <span className="pill staged">
              {staged.action === "reset" ? "reset" : staged.action} — unsaved
            </span>
          ) : line.needs_decision_open ? (
            <span className="pill needs">needs decision</span>
          ) : (
            <span className={`pill ${line.status}`}>{line.status}</span>
          )}
        </span>
      </button>

      {expanded && (
        <LineDetail
          line={line}
          staged={staged}
          errors={errors}
          onStage={onStage}
          onUnstage={onUnstage}
        />
      )}
    </div>
  );
}
