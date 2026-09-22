import { useState } from "react";

import { formatCount, formatUsd, plural } from "../money";
import { currentValues, describeGaps, FIELD_NAME, gapsFor, includeChange } from "../required";
import type { FieldValues, RequiredField } from "../required";
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
 *
 * If a row being included is missing a value it needs (a copy with no cost,
 * say), the card asks for it in place rather than staging a save that the
 * server could only refuse.
 */

type Typed = Record<string, Partial<FieldValues>>;

interface Asking {
  lines: OfferLine[];
  finish: (typed: Typed) => Change[];
}

export function ConflictGroup({
  members,
  staged,
  onKeep,
}: {
  members: OfferLine[];
  staged: Record<string, Change>;
  onKeep: (changes: Change[]) => void;
}) {
  const [asking, setAsking] = useState<Asking | null>(null);
  const [typed, setTyped] = useState<Typed>({});
  const [checked, setChecked] = useState(false);

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

  const valuesFor = (line: OfferLine, from: Typed): FieldValues => ({
    ...currentValues(line),
    ...from[line.line_id],
  });
  // With nothing typed this is exactly { line_id, action: "include" }.
  const include = (line: OfferLine, from: Typed): Change =>
    includeChange(line, valuesFor(line, from));
  const exclude = (line: OfferLine): Change => ({
    line_id: line.line_id,
    action: "exclude",
  });

  /** Stage now if every row to include is complete; otherwise ask first. */
  const attempt = (toInclude: OfferLine[], finish: (from: Typed) => Change[]) => {
    const incomplete = toInclude.filter(
      (line) => gapsFor(line, currentValues(line)).length > 0,
    );
    if (incomplete.length === 0) {
      setAsking(null);
      onKeep(finish({}));
      return;
    }
    setTyped({});
    setChecked(false);
    setAsking({ lines: incomplete, finish });
  };

  const countOnce = () => {
    const [first, ...rest] = members;
    attempt([first], (from) => [include(first, from), ...rest.map(exclude)]);
  };
  const countBoth = () => attempt(members, (from) => members.map((l) => include(l, from)));
  const keepOnly = (chosen: OfferLine) =>
    attempt([chosen], (from) =>
      members.map((line) =>
        line.line_id === chosen.line_id ? include(line, from) : exclude(line),
      ),
    );

  const finishAsking = () => {
    if (!asking) return;
    const stillMissing = asking.lines.some(
      (line) => gapsFor(line, valuesFor(line, typed)).length > 0,
    );
    if (stillMissing) {
      setChecked(true);
      return;
    }
    onKeep(asking.finish(typed));
    setAsking(null);
  };

  const stagedFor = (line: OfferLine) => staged[line.line_id];
  const allIncluded =
    members.length > 0 && members.every((line) => stagedFor(line)?.action === "include");
  const onceChosen =
    stagedFor(members[0])?.action === "include" &&
    members.slice(1).every((line) => stagedFor(line)?.action === "exclude");

  return (
    <div className="group k-needs">
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
        // Server-computed, so an excluded row still shows what it is worth.
        const value = line.line_value ?? line.would_be_line_value;
        return (
          <div key={line.line_id} className={`group-option${chosen ? " chosen" : ""}`}>
            <span>
              <strong>Row {line.source_row}</strong>
              <span className="muted">
                {" "}
                · {formatCount(line.quantity)} pieces · {formatUsd(line.unit_cost)} each ·{" "}
                {value === null ? "no total" : `${formatUsd(value)} total`}
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

      {asking && (
        <div className="ask-form">
          {asking.lines.map((line) => {
            const gaps = gapsFor(line, currentValues(line));
            const open = checked ? gapsFor(line, valuesFor(line, typed)) : [];
            return (
              <div key={line.line_id}>
                <p className="ask" role="alert">
                  To include row {line.source_row}, enter {describeGaps(gaps)}.
                  Nothing has been staged yet.
                </p>
                <div className="fields">
                  {gaps.map(({ field }) => (
                    <AskField
                      key={field}
                      label={`Row ${line.source_row} — ${FIELD_NAME[field]}`}
                      field={field}
                      value={typed[line.line_id]?.[field] ?? ""}
                      missing={open.some((gap) => gap.field === field)}
                      onChange={(value) =>
                        setTyped((current) => ({
                          ...current,
                          [line.line_id]: { ...current[line.line_id], [field]: value },
                        }))
                      }
                    />
                  ))}
                </div>
              </div>
            );
          })}
          <div className="row wrap">
            <button className="primary small" onClick={finishAsking}>
              Include with{" "}
              {plural(
                asking.lines.reduce(
                  (sum, line) => sum + gapsFor(line, currentValues(line)).length,
                  0,
                ),
                "this value",
                "these values",
              )}
            </button>
            <button className="small" onClick={() => setAsking(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

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

function AskField({
  label,
  field,
  value,
  missing,
  onChange,
}: {
  label: string;
  field: RequiredField;
  value: string;
  missing: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="ask-field">
      {label}
      <input
        type="text"
        inputMode={field === "quantity" ? "numeric" : field === "unit_cost" ? "decimal" : "text"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={missing || undefined}
        data-missing={missing ? "" : undefined}
        autoFocus
      />
    </label>
  );
}
