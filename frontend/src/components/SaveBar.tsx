import { plural } from "../money";
import type { Workspace } from "../hooks/useOfferWorkspace";

/**
 * Every state the save can be in, said plainly.
 *
 * The rule that shapes this: after a failure, Retry is the only save action.
 * Sending the staged edits instead would change the body under a reused
 * request id, and the server would rightly refuse it.
 */
export function SaveBar({
  workspace,
  faultArmed,
  onSave,
}: {
  workspace: Workspace;
  faultArmed: boolean;
  onSave: () => void;
}) {
  const {
    status,
    stagedCount,
    pendingCount,
    generalErrors,
    reapplyPrompt,
    canSave,
    canRetry,
  } = workspace;

  const tone =
    status.kind === "failed"
      ? "failed"
      : status.kind === "conflict"
        ? "conflict"
        : status.kind === "saved"
          ? "saved"
          : "";

  return (
    <div className={`savebar ${tone}`}>
      {reapplyPrompt && (
        <div className="reapply">
          <div className="inner">
            <strong>
              Re-apply your {reapplyPrompt.pending.length}{" "}
              {plural(reapplyPrompt.pending.length, "change")} on top?
            </strong>
            {reapplyPrompt.changedElsewhere.length > 0 ? (
              <>
                <p className="tiny" style={{ margin: "4px 0" }}>
                  Another window already changed{" "}
                  {reapplyPrompt.changedElsewhere.length}{" "}
                  {plural(reapplyPrompt.changedElsewhere.length, "line")} you
                  edited. Re-applying overwrites their decision:
                </p>
                <ul className="tiny">
                  {reapplyPrompt.changedElsewhere.map((lineId) => (
                    <li key={lineId}>
                      <span className="mono">{lineId}</span>
                    </li>
                  ))}
                </ul>
                <div className="row wrap">
                  <button
                    className="small"
                    onClick={() =>
                      workspace.reapply({ includeChangedElsewhere: false })
                    }
                  >
                    Re-apply the rest, leave those alone
                  </button>
                  <button
                    className="small danger"
                    onClick={() =>
                      workspace.reapply({ includeChangedElsewhere: true })
                    }
                  >
                    Overwrite all {reapplyPrompt.pending.length}
                  </button>
                  <button className="small" onClick={workspace.dismissReapply}>
                    Discard my changes
                  </button>
                </div>
              </>
            ) : (
              <div className="row wrap" style={{ marginTop: 6 }}>
                <button
                  className="primary small"
                  onClick={() =>
                    workspace.reapply({ includeChangedElsewhere: true })
                  }
                >
                  Re-apply them
                </button>
                <button className="small" onClick={workspace.dismissReapply}>
                  Discard my changes
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="savebar-inner">
        <div>
          {status.kind === "saving" && (
            <span className="savebar-message">Saving…</span>
          )}

          {status.kind === "failed" && (
            <span className="savebar-message bad">
              Not saved. Your changes are still here. {status.message}
            </span>
          )}

          {status.kind === "conflict" && (
            <span className="savebar-message warn">
              Changed in another window (now v{status.currentVersion}). Reload
              the latest to continue.
            </span>
          )}

          {status.kind === "saved" && pendingCount === 0 && (
            <span className="savebar-message good">
              Saved — v{status.version}
            </span>
          )}

          {(status.kind === "idle" ||
            (status.kind === "saved" && pendingCount > 0)) && (
            <span className="savebar-message">
              {pendingCount === 0
                ? "No unsaved changes"
                : `${pendingCount} unsaved ${plural(pendingCount, "change")}`}
            </span>
          )}

          {generalErrors.length > 0 && (
            <div className="tiny" style={{ color: "var(--stop)" }}>
              {generalErrors.join(" ")}
            </div>
          )}

          {status.kind === "failed" && stagedCount > 0 && (
            <div className="tiny muted">
              {stagedCount} newer {plural(stagedCount, "edit")} will be saved
              after this retry succeeds.
            </div>
          )}
        </div>

        <div className="row wrap">
          {faultArmed && status.kind !== "failed" && (
            <span className="tiny" style={{ color: "var(--warn)" }}>
              next save will fail
            </span>
          )}

          {status.kind === "conflict" && (
            <button className="primary" onClick={() => void workspace.reloadLatest()}>
              Reload latest
            </button>
          )}

          {canRetry && (
            <>
              <button className="primary" onClick={workspace.retry}>
                Retry
              </button>
              <button className="small" onClick={() => void workspace.reload()}>
                Check what was saved
              </button>
            </>
          )}

          {status.kind !== "failed" && status.kind !== "conflict" && (
            <>
              {stagedCount > 0 && (
                <button className="small" onClick={workspace.discardStaged}>
                  Discard
                </button>
              )}
              <button
                className="primary"
                disabled={!canSave}
                onClick={onSave}
              >
                {status.kind === "saving" ? "Saving…" : "Save"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
