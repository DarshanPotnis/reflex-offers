import { useRef, useState } from "react";
import type { RefObject } from "react";

import { describeGaps, gapsFor, includeChange } from "../required";
import type { FieldValues, RequiredField } from "../required";
import type { Change, OfferLine } from "../types";

/**
 * Include / exclude / reset for one line.
 *
 * The inputs start from the effective values the server sent. Only the
 * fields the user actually changed are sent as overrides, so the original
 * parse stays visible and a reset really does restore the default.
 *
 * Nothing is validated here beyond "is there something to send" (see
 * required.ts). The server is the validator, and its messages are what get
 * shown — a second, looser copy of the rules in the browser is how the two
 * drift apart. What is caught here is an include that could only be refused:
 * rather than stage it and fail at save, ask for the value right here.
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
  // Set once Include was refused for a missing value; the prompt then
  // follows what is typed and disappears when nothing is missing.
  const [asked, setAsked] = useState(false);

  const inputs: Record<RequiredField, RefObject<HTMLInputElement | null>> = {
    item_code: useRef<HTMLInputElement>(null),
    size: useRef<HTMLInputElement>(null),
    quantity: useRef<HTMLInputElement>(null),
    unit_cost: useRef<HTMLInputElement>(null),
  };
  const values: FieldValues = { item_code: itemCode, size, quantity, unit_cost: unitCost };
  const gaps = asked ? gapsFor(line, values) : [];
  const askId = `ask-${line.line_id}`;
  const missing = (field: RequiredField) => gaps.some((gap) => gap.field === field);
  const flag = (field: RequiredField) =>
    missing(field)
      ? { "aria-invalid": true as const, "aria-describedby": askId, "data-missing": "" }
      : {};

  const include = () => {
    const blocking = gapsFor(line, values);
    if (blocking.length > 0) {
      setAsked(true);
      inputs[blocking[0].field].current?.focus();
      return;
    }
    setAsked(false);
    onStage(includeChange(line, values, note));
  };

  const exclude = () => {
    const change: Change = { line_id: line.line_id, action: "exclude" };
    if (note.trim()) change.note = note.trim();
    setAsked(false);
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

      {gaps.length > 0 && (
        <p className="ask" id={askId} role="alert">
          To include this line, enter {describeGaps(gaps)}. Nothing has been
          staged yet.
        </p>
      )}

      <div className="fields">
        <label>
          Item code
          <input
            ref={inputs.item_code}
            type="text"
            value={itemCode}
            onChange={(e) => setItemCode(e.target.value)}
            {...flag("item_code")}
          />
        </label>
        <label>
          Size
          <input
            ref={inputs.size}
            type="text"
            className="narrow"
            value={size}
            onChange={(e) => setSize(e.target.value)}
            {...flag("size")}
          />
        </label>
        <label>
          Pieces
          <input
            ref={inputs.quantity}
            type="text"
            inputMode="numeric"
            className="narrow"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            {...flag("quantity")}
          />
        </label>
        <label>
          Supplier cost (USD)
          <input
            ref={inputs.unit_cost}
            type="text"
            inputMode="decimal"
            className="narrow"
            value={unitCost}
            onChange={(e) => setUnitCost(e.target.value)}
            {...flag("unit_cost")}
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
