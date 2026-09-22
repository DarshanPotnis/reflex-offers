import { formatCount, formatUsd } from "../money";
import type { Change, OfferLine } from "../types";

/**
 * A group of rows sharing an item and size, shown together so the choice is
 * one visible decision. Either way it stages ONE save covering every member,
 * so the group resolves in a single version bump and no sibling is left
 * sitting in Needs decision.
 *
 * The two kinds ask genuinely different questions:
 *
 *   duplicate  the numbers agree, so the only question is whether the
 *              supplier has one lot or two. Count once, or count both.
 *   conflict   the numbers disagree, so one of them is wrong. Pick one.
 */
export function ConflictGroup({
  members,
  staged,
  onKeep,
}: {
  members: OfferLine[];
  staged: Record<string, Change>;
  onKeep: (changes: Change[]) => void;
}) {
  const isDuplicate = members.some((line) =>
    line.issues.some(
      (issue) => issue.code === "DUPLICATE_ROW" || issue.code === "DUPLICATE_KEPT",
    ),
  );
  const rows = members.map((line) => line.source_row);
  const rowList =
    rows.length === 1
      ? `Row ${rows[0]}`
      : `Rows ${rows.slice(0, -1).join(", ")} and ${rows[rows.length - 1]}`;

  const headline =
    members[0].issues.find((issue) => issue.code === "CONFLICTING_ROWS")?.message ?? "";

  const include = (line: OfferLine): Change => ({
    line_id: line.line_id,
    action: "include",
  });
  const exclude = (line: OfferLine): Change => ({
    line_id: line.line_id,
    action: "exclude",
  });

  const countOnce = () => {
    const [first, ...rest] = members;
    onKeep([include(first), ...rest.map(exclude)]);
  };
  const countBoth = () => onKeep(members.map(include));
  const keepOnly = (chosen: OfferLine) =>
    onKeep(
      members.map((line) => (line.line_id === chosen.line_id ? include(line) : exclude(line))),
    );

  const stagedFor = (line: OfferLine) => staged[line.line_id];
  const allIncluded =
    members.length > 0 && members.every((line) => stagedFor(line)?.action === "include");
  const onceChosen =
    stagedFor(members[0])?.action === "include" &&
    members.slice(1).every((line) => stagedFor(line)?.action === "exclude");

  return (
    <div className="group">
      <h3>
        {isDuplicate
          ? `${rowList} are identical`
          : `${members[0].item_code} (${members[0].size}) — ${members.length} rows disagree`}
      </h3>
      {/* The heading already says they are identical; the parser's own
          wording underneath only repeated it. Conflicts still need theirs,
          because it names which values disagree. */}
      {!isDuplicate && headline && (
        <p className="tiny" style={{ marginTop: 0 }}>
          {headline}
        </p>
      )}

      {members.map((line) => {
        const pending = stagedFor(line);
        const chosen = pending?.action === "include";
        return (
          <div key={line.line_id} className={`group-option${chosen ? " chosen" : ""}`}>
            <span>
              <strong>Row {line.source_row}</strong>
              <span className="muted">
                {" "}
                · {formatCount(line.quantity)} pieces · {formatUsd(line.unit_cost)} each ·{" "}
                {formatUsd(line.line_value)} total
              </span>
              {line.description && <div className="tiny muted">{line.description}</div>}
            </span>
            <span className="row">
              {pending && <span className="pill staged">{pending.action} — unsaved</span>}
              {!isDuplicate && (
                <button
                  className={chosen ? "small" : "primary small"}
                  onClick={() => keepOnly(line)}
                  disabled={chosen}
                >
                  {chosen ? "Keeping this one" : "Keep this one"}
                </button>
              )}
            </span>
          </div>
        );
      })}

      {isDuplicate ? (
        <>
          <div className="row wrap" style={{ marginTop: 11 }}>
            <button
              className={onceChosen ? "small" : "primary small"}
              onClick={countOnce}
              disabled={onceChosen}
            >
              {onceChosen
                ? `Counting once (row ${rows[0]})`
                : `Count once — keep row ${rows[0]}`}
            </button>
            <button
              className="small"
              onClick={countBoth}
              disabled={allIncluded}
            >
              {allIncluded
                ? "Counting both"
                : "Count both — supplier has separate lots"}
            </button>
          </div>
          <p className="tiny muted" style={{ marginBottom: 0 }}>
            Counting once is the default, so stock is never counted twice.
          </p>
        </>
      ) : (
        <p className="tiny muted" style={{ marginBottom: 0 }}>
          Keeping one row excludes the others in the same save.
        </p>
      )}
    </div>
  );
}
