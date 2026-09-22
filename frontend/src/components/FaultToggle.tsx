/**
 * Only rendered when GET /api/config reports fault injection is on, so it
 * cannot appear in production.
 *
 * One-shot by design: it arms the next save and then disarms itself. If it
 * stayed armed the retry would fail too, and the recovery this exists to
 * demonstrate would never happen.
 */
export function FaultToggle({
  armed,
  onChange,
}: {
  armed: boolean;
  onChange: (armed: boolean) => void;
}) {
  return (
    <div className="fault-box">
      <label>
        <input
          type="checkbox"
          checked={armed}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>
          <strong>Test mode (enabled for this review)</strong>
          <br />
          <span>Fail the next save, after it commits.</span>
          <br />
          <span className="tiny muted">
            The save commits, then the server reports a failure — the dangerous
            case. Fires once, then disarms, so the retry succeeds and you can
            see the recovery.
          </span>
        </span>
      </label>
    </div>
  );
}
