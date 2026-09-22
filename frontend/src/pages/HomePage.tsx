import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, listOffers, uploadOffer } from "../api";
import { formatWhen } from "../money";
import type { OfferListing } from "../types";

/**
 * The layouts the reader accepts, by the column names it looks for. Kept in
 * step with NORTHSTAR_FIELDS / HARBOR_FIELDS in backend/app/core/reader.py.
 * These are column headings, not data: nothing here is a sample value.
 */
const LAYOUTS = [
  {
    name: "Northstar",
    shape: "One row per item and size.",
    columns: ["Item Code", "Size", "Units Available", "Cost USD"],
    optional: "Description, Retail USD and Category are read when present.",
  },
  {
    name: "Harbor",
    shape: "One row per style, with a column for each size.",
    columns: ["Style", "Unit Cost USD", "S", "M", "L", "…"],
    optional:
      "Product and Retail USD are read when present. A Total Units column is checked against the sizes, never used as the quantity.",
  },
];

const STEPS = [
  {
    title: "We read the sheet",
    body:
      "Columns are found by name, in any order. Formatting such as currency symbols, thousands separators and stray spaces is cleaned up, and every change is shown beside the original cell.",
  },
  {
    title: "You decide what we couldn't",
    body:
      "A missing price, a negative quantity or two rows that disagree could change what the offer is worth, so those lines stay out of the totals until you choose.",
  },
  {
    title: "Save, share, export",
    body:
      "Your decisions are saved at a link you can share. The Excel download is built from the saved offer, so it matches the screen.",
  },
];

export function HomePage() {
  const navigate = useNavigate();
  const [offers, setOffers] = useState<OfferListing[]>([]);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listOffers().then(setOffers).catch(() => setOffers([]));
  }, []);

  const send = useCallback(
    async (file: File) => {
      setBusy(true);
      setError(null);
      try {
        const receipt = await uploadOffer(file);
        navigate(`/offers/${receipt.offer_id}`);
      } catch (caught) {
        // The reader's message is written for a person; show it as-is.
        setError(
          caught instanceof ApiError ? caught.message : "That upload didn't work.",
        );
        setBusy(false);
      }
    },
    [navigate],
  );

  const choose = () => {
    if (!busy) fileInput.current?.click();
  };

  return (
    <div className="page home">
      <div className="masthead">
        <h1>Supplier offers</h1>
        <p>
          Upload a supplier's line sheet. You'll see what was cleaned up, what
          was left out and why, and what needs your decision — then save it as
          an offer you can share and export.
        </p>
      </div>

      <div
        className={`dropzone${over ? " over" : ""}${busy ? " busy" : ""}`}
        onClick={choose}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          const file = event.dataTransfer.files[0];
          if (file && !busy) void send(file);
        }}
      >
        <svg className="dropzone-icon" viewBox="0 0 48 48" aria-hidden="true">
          <rect x="9" y="5" width="30" height="38" rx="3" />
          <path d="M9 15h30M9 23h30M9 31h30M19 5v38" />
        </svg>
        <h2>{busy ? "Reading the sheet…" : "Drop a supplier line sheet here"}</h2>
        {!busy && <p className="muted">or</p>}
        <button
          type="button"
          className="primary"
          disabled={busy}
          onClick={(event) => {
            event.stopPropagation();
            choose();
          }}
        >
          Choose a file
        </button>
        <p className="muted tiny dropzone-limits">
          Excel .xlsx, up to 10 MB. The first sheet with a recognised header is
          read.
        </p>
        <input
          ref={fileInput}
          type="file"
          accept=".xlsx"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void send(file);
            event.target.value = "";
          }}
        />
      </div>

      {error && (
        <div className="banner error" style={{ marginTop: 14 }} role="alert">
          {error}
        </div>
      )}

      <h2 className="home-heading">Two supported layouts</h2>
      <div className="layouts">
        {LAYOUTS.map((layout) => (
          <div key={layout.name} className="card layout">
            <h3>{layout.name}</h3>
            <p className="layout-shape">{layout.shape}</p>
            <div className="layout-columns" aria-label={`${layout.name} columns`}>
              {layout.columns.map((column) => (
                <span key={column}>{column}</span>
              ))}
            </div>
            <p className="muted tiny">{layout.optional}</p>
          </div>
        ))}
      </div>
      <p className="muted tiny" style={{ margin: "8px 0 0" }}>
        Headings are matched by name, ignoring case and spacing, so reordered
        columns are fine. Title rows above the header and notes below the data
        are skipped and listed.
      </p>

      <h2 className="home-heading">What happens next</h2>
      <ol className="steps">
        {STEPS.map((entry) => (
          <li key={entry.title} className="card">
            <strong>{entry.title}</strong>
            <p className="muted tiny">{entry.body}</p>
          </li>
        ))}
      </ol>

      <h2 className="home-heading">Recent offers</h2>
      <div className="card">
        {offers.length === 0 ? (
          <p className="muted" style={{ margin: 0 }}>
            Nothing saved yet.
          </p>
        ) : (
          <ul className="offer-list">
            {offers.map((offer) => (
              <li key={offer.id}>
                <a href={`/offers/${offer.id}`}>
                  <span>
                    <strong>{offer.supplier_name ?? "Unknown supplier"}</strong>
                    <span className="muted tiny"> · {offer.source_filename}</span>
                  </span>
                  <span className="muted tiny">
                    v{offer.version} · {formatWhen(offer.created_at)}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
