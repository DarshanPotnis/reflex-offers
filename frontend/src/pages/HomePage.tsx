import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, listOffers, uploadOffer } from "../api";
import { formatWhen } from "../money";
import type { OfferListing } from "../types";

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

  return (
    <div className="page">
      <div className="masthead">
        <h1>Supplier offers</h1>
        <p>
          Upload a line sheet, check what it means, and save an offer you can
          come back to.
        </p>
      </div>

      <div
        className={`dropzone${over ? " over" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setOver(false);
          const file = event.dataTransfer.files[0];
          if (file) void send(file);
        }}
      >
        <h2>{busy ? "Reading the sheet…" : "Drop a supplier line sheet here"}</h2>
        <p className="muted tiny">
          .xlsx · Northstar or Harbor layout · up to 10 MB. Columns can be in
          any order.
        </p>
        <button
          className="primary"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          Choose a file
        </button>
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

      <h2 style={{ fontSize: 16, margin: "26px 0 10px" }}>Recent offers</h2>
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
