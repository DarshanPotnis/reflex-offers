import type {
  Change,
  Config,
  LineErrors,
  Offer,
  OfferListing,
  Receipt,
} from "./types";

/**
 * What went wrong, in a shape the save state machine can branch on.
 *
 * `kind` matters more than the status code: a dropped connection and a 503
 * are the same situation to the user ("not saved, your changes are here"),
 * while a 409 and a 422 need completely different offers of help.
 */
export type ApiFailure =
  | { kind: "conflict"; currentVersion: number; message: string }
  | { kind: "rejected"; errors: LineErrors[]; message: string }
  | { kind: "missing"; message: string }
  | { kind: "failed"; message: string };

export class ApiError extends Error {
  readonly failure: ApiFailure;

  constructor(failure: ApiFailure) {
    super(failure.message);
    this.name = "ApiError";
    this.failure = failure;
  }
}

async function toFailure(response: Response): Promise<ApiFailure> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  const record = (body ?? {}) as Record<string, unknown>;
  const detail = typeof record.detail === "string" ? record.detail : null;

  if (response.status === 409) {
    return {
      kind: "conflict",
      currentVersion: Number(record.current_version ?? 0),
      message: detail ?? "This offer changed in another window.",
    };
  }
  if (response.status === 422 && Array.isArray(record.errors)) {
    const errors = record.errors as LineErrors[];
    return {
      kind: "rejected",
      errors,
      message: errors[0]?.messages[0] ?? "Some changes were rejected.",
    };
  }
  if (response.status === 404) {
    return { kind: "missing", message: detail ?? "Not found." };
  }
  return {
    kind: "failed",
    message: detail ?? `The server returned ${response.status}.`,
  };
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    // Offline, DNS, connection reset: indistinguishable from a 5xx to the
    // user, and recovered the same way.
    throw new ApiError({
      kind: "failed",
      message: "Couldn't reach the server.",
    });
  }
  if (!response.ok) throw new ApiError(await toFailure(response));
  return (await response.json()) as T;
}

export const getConfig = () => request<Config>("/api/config");

export const getOffer = (id: string) =>
  request<Offer>(`/api/offers/${encodeURIComponent(id)}`);

export const listOffers = () =>
  request<{ offers: OfferListing[] }>("/api/offers").then((r) => r.offers);

export function uploadOffer(file: File): Promise<Offer> {
  const body = new FormData();
  body.append("file", file);
  return request<Offer>("/api/offers", { method: "POST", body });
}

export function saveDecisions(
  offerId: string,
  payload: { requestId: string; baseVersion: number; changes: Change[] },
  options: { failNextSave?: boolean } = {},
): Promise<Receipt> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (options.failNextSave) headers["X-Fault"] = "after-commit";

  return request<Receipt>(
    `/api/offers/${encodeURIComponent(offerId)}/decisions`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        request_id: payload.requestId,
        base_version: payload.baseVersion,
        changes: payload.changes,
      }),
    },
  );
}

/** The one the button uses: Excel keeps 000101 a code, not the number 101. */
export const exportUrl = (id: string) =>
  `/api/offers/${encodeURIComponent(id)}/export.xlsx`;

/** Kept for imports and for byte-exact decimal amounts. */
export const exportCsvUrl = (id: string) =>
  `/api/offers/${encodeURIComponent(id)}/export.csv`;

export const sourceUrl = (id: string) =>
  `/api/offers/${encodeURIComponent(id)}/source`;
