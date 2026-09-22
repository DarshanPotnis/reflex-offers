import { formatCount, formatUsd } from "../money";
import type { Change, OfferLine } from "../types";
import { IssueList } from "./IssueList";
import { LineEditor } from "./LineEditor";

const CELL_LABELS: Record<string, string> = {
  item_code: "Item code",
  description: "Description",
  size: "Size",
  category: "Category",
  quantity: "Pieces",
  unit_cost: "Supplier cost",
  retail: "Retail",
};

/** Show the raw cell as the sheet holds it, quoting text so stray spaces show. */
function rawText(raw: string | number | boolean | null): string {
  if (raw === null || raw === undefined || raw === "") return "(empty)";
  if (typeof raw === "string") return `"${raw}"`;
  return String(raw);
}

export function LineDetail({
  line,
  staged,
  errors,
  onStage,
  onUnstage,
}: {
  line: OfferLine;
  staged: Change | undefined;
  errors: string[];
  onStage: (change: Change) => void;
  onUnstage: () => void;
}) {
  const cells = Object.entries(line.cells).filter(([name]) => name in CELL_LABELS);

  return (
    <div className="detail">
      <div className="compare">
        <div>
          <h4>From the sheet</h4>
          <table>
            <tbody>
              {cells.map(([name, cell]) => (
                <tr key={name}>
                  <td>{CELL_LABELS[name]}</td>
                  <td>
                    <span className="mono coord">{cell.coordinate}</span>{" "}
                    <span className="mono">{rawText(cell.raw)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <h4>What we read it as</h4>
          <table>
            <tbody>
              <tr>
                <td>Item code</td>
                <td>{line.item_code ?? <em className="muted">none</em>}</td>
              </tr>
              <tr>
                <td>Description</td>
                <td>{line.description ?? <em className="muted">none</em>}</td>
              </tr>
              <tr>
                <td>Size</td>
                <td>{line.size ?? <em className="muted">none</em>}</td>
              </tr>
              <tr>
                <td>Pieces</td>
                <td className="num">
                  {line.quantity === null ? (
                    <em className="muted">none</em>
                  ) : (
                    formatCount(line.quantity)
                  )}
                </td>
              </tr>
              <tr>
                <td>Supplier cost</td>
                <td className="num">{formatUsd(line.unit_cost)}</td>
              </tr>
              <tr>
                <td>Line value</td>
                <td className="num">
                  <strong>{formatUsd(line.line_value)}</strong>
                </td>
              </tr>
              <tr>
                <td>Retail reference</td>
                <td className="num muted">{formatUsd(line.retail)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <p className="tiny muted" style={{ marginTop: 0 }}>
        {line.status_reason}
      </p>

      <IssueList issues={line.issues} />

      <LineEditor
        line={line}
        staged={staged}
        errors={errors}
        onStage={onStage}
        onUnstage={onUnstage}
      />
    </div>
  );
}
