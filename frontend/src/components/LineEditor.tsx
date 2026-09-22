import { useState } from "react";

import type { Change, OfferLine } from "../types";

/**
 * Include / exclude / reset for one line.
 *
 * The inputs start from the effective values the server sent. Only the
 * fields the user actually changed are sent as overrides, so the original
 * parse stays visible and a reset really does restore the default.
 *
 * Nothing is validated here beyond "did you type something". The server is
 * the validator, and its messages are what get shown — a second, looser copy
 * of the rules in the browser is how the two drift apart.
 */
export function LineEditor({
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
  const [itemCode, setItemCode] = useState(staged?.item_code ?? line.item_code ?? "");
  const [size, setSize] = useState(staged?.size ?? line.size ?? "");
  const [quantity, setQuantity] = useState(
    staged?.quantity?.toString() ?? line.quantity?.toString() ?? "",
  );
  const [unitCost, setUnitCost] = useState(staged?.unit_cost ?? line.unit_cost ?? "");
  const [note, setNote] = useState(staged?.note ?? line.decision?.note ?? "");

  const include = () => {
    const change: Change = { line_id: line.line_id, action: "include" };
    if (itemCode.trim() !== (line.item_code ?? "")) change.item_code = itemCode.trim();
    if (size.trim() !== (line.size ?? "")) change.size = size.trim();
    if (quantity.trim() !== (line.quantity?.toString() ?? "")) {
      // Sent only when it is a whole number; anything else goes as-is so the
      // server can explain what is wrong with it in its own words.
      const whole = /^-?\d+$/.test(quantity.trim());
      change.quantity = whole
        ? Number.parseInt(quantity.trim(), 10)
        : (quantity.trim() as unknown as number);
    }
    if (unitCost.trim() !== (line.unit_cost ?? "")) change.unit_cost = unitCost.trim();
    if (note.trim()) change.note = note.trim();
    onStage(change);
  };

  const exclude = () => {
    const change: Change = { line_id: line.line_id, action: "exclude" };
    if (note.trim()) change.note = note.trim();
    onStage(change);
  };

  return (
    <div className="editor">
      {errors.length > 0 && (
        <ul className="field-errors" role="alert">
          {errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}

      <div className="fields">
        <label>
          Item code
          <input
            type="text"
            value={itemCode}
            onChange={(e) => setItemCode(e.target.value)}
          />
        </label>
        <label>
          Size
          <input
            type="text"
            className="narrow"
            value={size}
            onChange={(e) => setSize(e.target.value)}
          />
        </label>
        <label>
          Pieces
          <input
            type="text"
            inputMode="numeric"
            className="narrow"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
        </label>
        <label>
          Supplier cost (USD)
          <input
            type="text"
            inputMode="decimal"
            className="narrow"
            value={unitCost}
            onChange={(e) => setUnitCost(e.target.value)}
          />
        </label>
        <label style={{ flex: 1, minWidth: 190 }}>
          Note (optional)
          <input
            type="text"
            style={{ width: "100%" }}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why you made this call"
          />
        </label>
      </div>

      <div className="row wrap">
        <button className="primary small" onClick={include}>
          Include
        </button>
        <button className="small" onClick={exclude}>
          Exclude
        </button>
        <button
          className="small"
          onClick={() => onStage({ line_id: line.line_id, action: "reset" })}
          disabled={line.decision === null && !staged}
        >
          Reset to default
        </button>
        {staged && (
          <button className="small danger" onClick={onUnstage}>
            Undo this edit
          </button>
        )}
      </div>
    </div>
  );
}
