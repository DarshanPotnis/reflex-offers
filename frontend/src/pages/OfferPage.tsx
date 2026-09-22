import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { exportCsvUrl, exportUrl, getConfig, sourceUrl } from "../api";
import { ConflictGroup } from "../components/ConflictGroup";
import { FaultToggle } from "../components/FaultToggle";
import { LineList } from "../components/LineList";
import { NoticesPanel } from "../components/NoticesPanel";
import { SaveBar } from "../components/SaveBar";
import { SummaryPanel } from "../components/SummaryPanel";
import { useOfferWorkspace } from "../hooks/useOfferWorkspace";
import { formatCount, plural } from "../money";
import type { Change, OfferLine } from "../types";

type TabId = "needs" | "warnings" | "fixed" | "excluded" | "all";

const TAB_INTRO: Record<TabId, string> = {
  needs: "Left out until you decide. Nothing here is counted in the totals.",
  warnings: "Counted, but worth a look before you send this on.",
  fixed: "Read differently from the sheet, with the meaning unchanged. Every change is shown and reversible.",
  excluded: "Everything currently out of the totals, and why.",
  all: "Every line the sheet produced.",
};

export function OfferPage() {
  const { offerId = "" } = useParams();
  const workspace = useOfferWorkspace(offerId);
  const { offer, staged, lineErrors, linesById } = workspace;

  const [tab, setTab] = useState<TabId>("needs");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [faultAvailable, setFaultAvailable] = useState(false);
  const [faultArmed, setFaultArmed] = useState(false);

  useEffect(() => {
    getConfig()
      .then((config) => setFaultAvailable(config.fault_injection))
      .catch(() => setFaultAvailable(false));
  }, []);

  const buckets = useMemo(() => {
    const lines = offer?.lines ?? [];
    const has = (line: OfferLine, kind: string) =>
      line.issues.some((issue) => issue.kind === kind);
    return {
      needs: lines.filter((line) => line.needs_decision_open),
      warnings: lines.filter((line) => has(line, "warning")),
      fixed: lines.filter((line) => has(line, "fixed")),
      excluded: lines.filter((line) => line.status === "excluded"),
      all: lines,
    } satisfies Record<TabId, OfferLine[]>;
  }, [offer]);

  // Conflict and duplicate groups are shown together, so the choice between
  // them is a single visible decision rather than several separate ones.
  const { groups, ungrouped } = useMemo(() => {
    const seen = new Set<string>();
    const groups: OfferLine[][] = [];
    const ungrouped: OfferLine[] = [];
    for (const line of buckets.needs) {
      if (seen.has(line.line_id)) continue;
      if (line.related_line_ids.length === 0) {
        ungrouped.push(line);
        continue;
      }
      const members = [line, ...line.related_line_ids
        .map((id) => linesById.get(id))
        .filter((l): l is OfferLine => Boolean(l))]
        .sort((a, b) => a.source_row - b.source_row);
      for (const member of members) seen.add(member.line_id);
      groups.push(members);
    }
    return { groups, ungrouped };
  }, [buckets.needs, linesById]);

  const toggle = (lineId: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });

  const stageGroup = (changes: Change[]) => workspace.stage(...changes);

  const handleSave = () => {
    const armed = faultArmed;
    setFaultArmed(false); // one-shot: the retry must be able to succeed
    workspace.save(armed);
  };

  if (workspace.loadError) {
    return (
      <div className="page">
        <div className="banner error">{workspace.loadError}</div>
        <Link to="/">← Back to uploads</Link>
      </div>
    );
  }
  if (!offer) {
    return (
      <div className="page">
        <p className="muted">Loading…</p>
      </div>
    );
  }

  const tabs: { id: TabId; label: string; count: number }[] = [
    { id: "needs", label: "Needs decision", count: buckets.needs.length },
    { id: "warnings", label: "Warnings", count: buckets.warnings.length },
    { id: "fixed", label: "Auto-fixed", count: buckets.fixed.length },
    { id: "excluded", label: "Excluded", count: buckets.excluded.length },
    { id: "all", label: "All lines", count: buckets.all.length },
  ];

  return (
    <div className="page">
      <div className="offer-head spread">
        <div>
          <Link to="/" className="tiny">
            ← All offers
          </Link>
          <h1>{offer.supplier_name ?? "Supplier offer"}</h1>
          <p className="muted tiny" style={{ margin: "2px 0 0" }}>
            {offer.source_filename} · sheet “{offer.sheet_name}” ·{" "}
            {offer.layout} layout · v{offer.version}
          </p>
        </div>
        <div className="row wrap">
          <a className="tiny" href={sourceUrl(offer.id)}>
            Original file
          </a>
          {workspace.hasUnsaved ? (
            <>
              {/* Said out loud, not as a tooltip: nobody hovers a disabled
                  button, so a greyed-out one with no reason reads as broken. */}
              <span className="tiny export-hint">
                Save your {workspace.pendingCount}{" "}
                {plural(workspace.pendingCount, "change")} first so the file
                matches what's saved.
              </span>
              <button disabled>Export Excel</button>
            </>
          ) : (
            <>
              <a href={exportUrl(offer.id)}>
                <button className="primary">Export Excel</button>
              </a>
              <a className="tiny" href={exportCsvUrl(offer.id)}>
                CSV
              </a>
            </>
          )}
        </div>
      </div>

      <SummaryPanel summary={offer.summary} />
      <NoticesPanel notices={offer.notices} />
      {faultAvailable && (
        <FaultToggle armed={faultArmed} onChange={setFaultArmed} />
      )}

      <div className="tabs" role="tablist">
        {tabs.map((entry) => (
          <button
            key={entry.id}
            role="tab"
            aria-selected={tab === entry.id}
            onClick={() => setTab(entry.id)}
          >
            {entry.label}
            <span className="tab-count">{formatCount(entry.count)}</span>
          </button>
        ))}
      </div>

      <div className="tab-panel" role="tabpanel">
        <p className="tab-intro tiny">{TAB_INTRO[tab]}</p>

        {tab === "needs" ? (
          <>
            {groups.map((members) => (
              <ConflictGroup
                key={members.map((m) => m.line_id).join("+")}
                members={members}
                staged={staged}
                onKeep={stageGroup}
              />
            ))}
            <LineList
              lines={ungrouped}
              expanded={expanded}
              staged={staged}
              linesById={linesById}
              lineErrors={lineErrors}
              onToggle={toggle}
              onStage={workspace.stage}
              onUnstage={workspace.unstage}
              empty={
                groups.length > 0
                  ? "Nothing else needs a decision."
                  : "Nothing needs a decision — this sheet read cleanly."
              }
            />
          </>
        ) : (
          <LineList
            lines={buckets[tab]}
            expanded={expanded}
            staged={staged}
            linesById={linesById}
            lineErrors={lineErrors}
            onToggle={toggle}
            onStage={workspace.stage}
            onUnstage={workspace.unstage}
            empty={`No lines here (${buckets[tab].length} of ${formatCount(
              offer.summary.total_lines,
            )} ${plural(offer.summary.total_lines, "line")}).`}
          />
        )}
      </div>

      <SaveBar workspace={workspace} faultArmed={faultArmed} onSave={handleSave} />
    </div>
  );
}
