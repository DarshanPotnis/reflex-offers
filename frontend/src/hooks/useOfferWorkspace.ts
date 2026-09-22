import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, getOffer, saveDecisions } from "../api";
import type { Change, LineDecision, Offer } from "../types";

/**
 * The offer plus the save state machine, which is the whole point of the
 * page. Three pieces of state that must never be conflated:
 *
 *   staged    edits made but not submitted
 *   inFlight  the exact batch being saved or failed
 *   status    idle / saving / saved / failed / conflict
 *
 * A retry resends `inFlight` byte-identically, down to its base_version,
 * because the server hashes the whole body and answers "request_id reused
 * with different changes" if any of it moves. A new request id is minted
 * only after a confirmed success.
 */

export type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; version: number }
  | { kind: "failed"; message: string }
  | { kind: "conflict"; currentVersion: number };

interface InFlight {
  requestId: string;
  baseVersion: number;
  changes: Change[];
}

interface ReapplyPrompt {
  pending: Change[];
  changedElsewhere: string[];
}

const storageKey = (offerId: string) => `reflex-offer:${offerId}`;

function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Older Safari over plain http; the server only needs a valid uuid.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function sameDecision(a: LineDecision | null, b: LineDecision | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    a.action === b.action &&
    a.item_code === b.item_code &&
    a.size === b.size &&
    a.quantity === b.quantity &&
    a.unit_cost === b.unit_cost &&
    a.note === b.note
  );
}

export function useOfferWorkspace(offerId: string) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [staged, setStaged] = useState<Record<string, Change>>({});
  const [inFlight, setInFlight] = useState<InFlight | null>(null);
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });
  const [lineErrors, setLineErrors] = useState<Record<string, string[]>>({});
  const [generalErrors, setGeneralErrors] = useState<string[]>([]);
  const [reapplyPrompt, setReapplyPrompt] = useState<ReapplyPrompt | null>(null);

  // The offer the user was working from, for spotting what another window
  // changed underneath them.
  const baseSnapshot = useRef<Offer | null>(null);

  // ---- persistence -------------------------------------------------------
  // Survives a refresh mid-failure so Retry can still reuse the request id.

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(storageKey(offerId));
      if (!raw) return;
      const saved = JSON.parse(raw) as {
        staged?: Record<string, Change>;
        inFlight?: InFlight | null;
      };
      if (saved.staged) setStaged(saved.staged);
      if (saved.inFlight) {
        setInFlight(saved.inFlight);
        setStatus({
          kind: "failed",
          message: "This save didn't finish before the page reloaded.",
        });
      }
    } catch {
      /* private mode, or corrupt: start clean */
    }
  }, [offerId]);

  useEffect(() => {
    try {
      const empty = Object.keys(staged).length === 0 && inFlight === null;
      if (empty) sessionStorage.removeItem(storageKey(offerId));
      else {
        sessionStorage.setItem(
          storageKey(offerId),
          JSON.stringify({ staged, inFlight }),
        );
      }
    } catch {
      /* storage unavailable: the page still works, just not across reloads */
    }
  }, [offerId, staged, inFlight]);

  // ---- loading -----------------------------------------------------------

  const load = useCallback(async () => {
    try {
      const fresh = await getOffer(offerId);
      setOffer(fresh);
      baseSnapshot.current = fresh;
      setLoadError(null);
      return fresh;
    } catch (error) {
      const message =
        error instanceof ApiError ? error.message : "Couldn't load this offer.";
      setLoadError(message);
      return null;
    }
  }, [offerId]);

  useEffect(() => {
    void load();
  }, [load]);

  // ---- staging -----------------------------------------------------------

  const stage = useCallback((...changes: Change[]) => {
    if (changes.length === 0) return;
    setStatus((current) => (current.kind === "saved" ? { kind: "idle" } : current));
    setStaged((current) => {
      const next = { ...current };
      for (const change of changes) next[change.line_id] = change;
      return next;
    });
    setLineErrors((current) => {
      const next = { ...current };
      for (const change of changes) delete next[change.line_id];
      return next;
    });
  }, []);

  const unstage = useCallback((lineId: string) => {
    setStaged((current) => {
      const next = { ...current };
      delete next[lineId];
      return next;
    });
  }, []);

  const discardStaged = useCallback(() => {
    setStaged({});
    setLineErrors({});
    setGeneralErrors([]);
  }, []);

  // ---- saving ------------------------------------------------------------

  const send = useCallback(
    async (batch: InFlight, failNextSave: boolean) => {
      setStatus({ kind: "saving" });
      setGeneralErrors([]);
      try {
        await saveDecisions(
          offerId,
          {
            requestId: batch.requestId,
            baseVersion: batch.baseVersion,
            changes: batch.changes,
          },
          { failNextSave },
        );
        // Refetch before reporting success: totals and lines must always
        // arrive in one response rather than being patched locally.
        const fresh = await load();
        setInFlight(null);
        setLineErrors({});
        setStatus({ kind: "saved", version: fresh?.version ?? batch.baseVersion + 1 });
      } catch (error) {
        if (!(error instanceof ApiError)) {
          setInFlight(batch);
          setStatus({ kind: "failed", message: "Something went wrong." });
          return;
        }
        const failure = error.failure;
        if (failure.kind === "rejected") {
          // The values need fixing, so the body will change: this request id
          // must not be reused. Put the batch back for editing.
          const perLine: Record<string, string[]> = {};
          const general: string[] = [];
          for (const entry of failure.errors) {
            if (entry.line_id) perLine[entry.line_id] = entry.messages;
            else general.push(...entry.messages);
          }
          setStaged((current) => {
            const next = { ...current };
            for (const change of batch.changes) {
              if (!(change.line_id in next)) next[change.line_id] = change;
            }
            return next;
          });
          setInFlight(null);
          setLineErrors(perLine);
          setGeneralErrors(general);
          setStatus({ kind: "idle" });
          return;
        }
        if (failure.kind === "conflict") {
          setInFlight(batch);
          setStatus({ kind: "conflict", currentVersion: failure.currentVersion });
          return;
        }
        setInFlight(batch);
        setStatus({ kind: "failed", message: failure.message });
      }
    },
    [load, offerId],
  );

  const save = useCallback(
    (failNextSave = false) => {
      if (!offer) return;
      const changes = Object.values(staged);
      if (changes.length === 0) return;
      const batch: InFlight = {
        requestId: newRequestId(),
        baseVersion: offer.version,
        changes,
      };
      setInFlight(batch);
      setStaged({});
      void send(batch, failNextSave);
    },
    [offer, send, staged],
  );

  /** Resends the failed batch exactly: same id, same base version, same body. */
  const retry = useCallback(() => {
    if (!inFlight) return;
    void send(inFlight, false);
  }, [inFlight, send]);

  // ---- 409 recovery ------------------------------------------------------

  const reloadLatest = useCallback(async () => {
    const previous = baseSnapshot.current;
    const fresh = await getOffer(offerId).catch(() => null);
    if (!fresh) {
      setStatus({ kind: "failed", message: "Couldn't reload this offer." });
      return;
    }

    // Everything still unsaved: the failed batch plus anything typed since.
    const pendingByLine: Record<string, Change> = {};
    for (const change of inFlight?.changes ?? []) pendingByLine[change.line_id] = change;
    for (const change of Object.values(staged)) pendingByLine[change.line_id] = change;
    const pending = Object.values(pendingByLine);

    const previousLines = new Map((previous?.lines ?? []).map((l) => [l.line_id, l]));
    const freshLines = new Map(fresh.lines.map((l) => [l.line_id, l]));
    const changedElsewhere = pending
      .map((change) => change.line_id)
      .filter((lineId) => {
        const before = previousLines.get(lineId);
        const after = freshLines.get(lineId);
        if (!before || !after) return true;
        return !sameDecision(before.decision, after.decision);
      });

    setOffer(fresh);
    baseSnapshot.current = fresh;
    setInFlight(null);
    setStaged({});
    setStatus({ kind: "idle" });
    setReapplyPrompt(pending.length > 0 ? { pending, changedElsewhere } : null);
  }, [inFlight, offerId, staged]);

  /** Re-stage the pending edits on top of the freshly loaded offer. */
  const reapply = useCallback(
    (options: { includeChangedElsewhere: boolean }) => {
      setReapplyPrompt((prompt) => {
        if (!prompt) return null;
        const skip = options.includeChangedElsewhere
          ? new Set<string>()
          : new Set(prompt.changedElsewhere);
        const next: Record<string, Change> = {};
        for (const change of prompt.pending) {
          if (!skip.has(change.line_id)) next[change.line_id] = change;
        }
        // A new base version and a new body: genuinely a new save, so the
        // next save() mints a fresh request id.
        setStaged(next);
        return null;
      });
    },
    [],
  );

  const dismissReapply = useCallback(() => {
    setReapplyPrompt(null);
  }, []);

  // ---- derived -----------------------------------------------------------

  const stagedCount = Object.keys(staged).length;
  const pendingCount = stagedCount + (inFlight?.changes.length ?? 0);
  const hasUnsaved = pendingCount > 0;

  useEffect(() => {
    if (!hasUnsaved) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasUnsaved]);

  const linesById = useMemo(
    () => new Map((offer?.lines ?? []).map((line) => [line.line_id, line])),
    [offer],
  );

  return {
    offer,
    loadError,
    linesById,
    staged,
    stagedCount,
    pendingCount,
    hasUnsaved,
    status,
    lineErrors,
    generalErrors,
    reapplyPrompt,
    // While a save has failed, Retry is the only way forward: sending the
    // staged edits would change the body under a reused request id.
    canSave: status.kind !== "saving" && status.kind !== "failed" && stagedCount > 0,
    canRetry: status.kind === "failed" && inFlight !== null,
    stage,
    unstage,
    discardStaged,
    save,
    retry,
    reloadLatest,
    reapply,
    dismissReapply,
    reload: load,
  };
}

export type Workspace = ReturnType<typeof useOfferWorkspace>;
