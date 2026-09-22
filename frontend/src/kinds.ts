import type { IssueKind, OfferLine } from "./types";

/**
 * One label and one colour per kind, used by the summary, the tabs, the row
 * badges and the issue tags alike, so the page is read with four colours
 * learned once.
 *
 * "Needs decision" rather than "needs you": it is the word the summary, the
 * review button and the progress count all use ("7 need your decision",
 * "Review 7 decisions", "3 of 7 decided").
 */
export const KIND: Record<IssueKind, { label: string; className: string }> = {
  fixed: { label: "Cleaned up", className: "k-fixed" },
  warning: { label: "Worth a look", className: "k-warn" },
  auto_excluded: { label: "Left out", className: "k-out" },
  needs_decision: { label: "Needs decision", className: "k-needs" },
};

/** Counted in the totals. Not an issue kind, but it gets a colour too. */
export const READY = { label: "Ready", className: "k-ready" };

export const hasKind = (line: OfferLine, kind: IssueKind): boolean =>
  line.issues.some((issue) => issue.kind === kind);

/** Out of the totals and not waiting on anyone: left out by a rule or by you. */
export const isLeftOut = (line: OfferLine): boolean =>
  line.status === "excluded" && !line.needs_decision_open;

/**
 * The sheet raised a question about this line. Issues are frozen at upload,
 * so the number of these never moves; how many are still open does.
 */
export const wasAskedAbout = (line: OfferLine): boolean =>
  hasKind(line, "needs_decision");
