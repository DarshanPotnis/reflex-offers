import { hasKind, isLeftOut, KIND, READY, wasAskedAbout } from "../kinds";
import { formatCount, plural } from "../money";
import { lineReason } from "../reason";
import type { Change, Offer, OfferLine } from "../types";

/**
 * The first thing on the page: what the sheet held, what was done with it
 * automatically, and what is still needed from the person reading.
 *
 * Every number is the server's. The counts come from `summary`, or are counts
 * of the lines it sent; nothing here does arithmetic on money.
 *
 * The top level must add up to the lines read: ready + left out + need your
 * decision partition them. "Cleaned up" and "worth a look" sit inside ready,
 * so they count *ready* lines only. That is deliberately not summary.fixed or
 * summary.warnings, which count lines of any status (Harbor's -3 row carries a
 * warning and is waiting on a decision); nesting those would claim ready lines
 * that aren't. The tabs keep the all-status counts, because a tab is a filter.
 */

function leftOutReason(line: OfferLine, linesById: Map<string, OfferLine>): string {
  if (line.decision?.action === "exclude") return "excluded by you";
  return lineReason(line, linesById) ?? "left out";
}

function reasonSummary(counts: Map<string, number>): string {
  if (counts.size === 0) return "";
  if (counts.size === 1) return ` (${[...counts.keys()][0]})`;
  return ` (${[...counts].map(([reason, n]) => `${reason}: ${formatCount(n)}`).join(", ")})`;
}

export function OfferOverview({
  offer,
  linesById,
  staged,
  hasUnsaved,
  onReview,
}: {
  offer: Offer;
  linesById: Map<string, OfferLine>;
  staged: Record<string, Change>;
  hasUnsaved: boolean;
  onReview: () => void;
}) {
  const { summary, lines } = offer;

  const ready = lines.filter((line) => line.status === "included");
  const cleanedUp = ready.filter((line) => hasKind(line, "fixed")).length;
  const worthALook = ready.filter((line) => hasKind(line, "warning")).length;
  const within = [
    cleanedUp > 0 && (
      <span key="fixed" className={`within ${KIND.fixed.className}`} data-kind="fixed">
        <b>{formatCount(cleanedUp)}</b> cleaned up automatically
      </span>
    ),
    worthALook > 0 && (
      <span key="warning" className={`within ${KIND.warning.className}`} data-kind="warning">
        <b>{formatCount(worthALook)}</b> worth a look
      </span>
    ),
  ].filter(Boolean);

  const leftOut = lines.filter(isLeftOut);
  const reasons = new Map<string, number>();
  for (const line of leftOut) {
    const reason = leftOutReason(line, linesById);
    reasons.set(reason, (reasons.get(reason) ?? 0) + 1);
  }

  const open = summary.needs_decision_open;
  const asked = lines.filter(wasAskedAbout).length;
  const decided = asked - open;
  const waiting = lines.filter((line) => {
    const change = staged[line.line_id];
    return line.needs_decision_open && change !== undefined && change.action !== "reset";
  }).length;

  return (
    <section className="card overview" aria-label="What was found in this sheet">
      <p className="overview-lead">
        Read <strong>{formatCount(summary.total_lines)}</strong>{" "}
        {plural(summary.total_lines, "line")} from{" "}
        <strong>{offer.source_filename ?? "the uploaded file"}</strong>.
      </p>

      <ul className="chips">
        <li className={`chip ${READY.className}`} data-kind="ready">
          <b>{formatCount(summary.included_lines)}</b> ready to go
          {within.length > 0 && (
            <>
              {" ("}
              {within.flatMap((part, index) => (index === 0 ? [part] : [", ", part]))}
              {")"}
            </>
          )}
        </li>
        <li className={`chip ${KIND.auto_excluded.className}`} data-kind="left-out">
          <b>{formatCount(leftOut.length)}</b> left out{reasonSummary(reasons)}
        </li>
        <li className={`chip ${KIND.needs_decision.className}`} data-kind="needs">
          <b>{formatCount(open)}</b> {open === 1 ? "needs" : "need"} your decision
        </li>
      </ul>

      <div className="decisions">
        {asked === 0 ? (
          <p className="decisions-done">
            Nothing in this sheet needs a decision
            {hasUnsaved ? ". Save your changes, then export." : " — ready to export."}
          </p>
        ) : open === 0 ? (
          <p className="decisions-done">
            {hasUnsaved
              ? "All decisions made. Save your changes, then export."
              : "All decisions made — ready to export"}
          </p>
        ) : (
          <>
            <div className="decisions-count">
              <strong>
                {formatCount(decided)} of {formatCount(asked)} decided
              </strong>
              {waiting > 0 && (
                <span className="muted tiny">
                  {" "}
                  · {formatCount(waiting)} more chosen, not saved yet
                </span>
              )}
            </div>
            <div
              className="progress"
              role="progressbar"
              aria-label="Decisions made"
              aria-valuemin={0}
              aria-valuemax={asked}
              aria-valuenow={decided}
            >
              <span style={{ width: `${(decided / asked) * 100}%` }} />
            </div>
            <button className="primary" onClick={onReview}>
              Review {formatCount(open)} {plural(open, "decision")} →
            </button>
          </>
        )}
      </div>
    </section>
  );
}
