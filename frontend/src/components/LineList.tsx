import { useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import type { Change, OfferLine } from "../types";
import { LineRow, LineTableHeader } from "./LineRow";

/** Above this, render only what is on screen. The 5,000-row sheet needs it. */
const VIRTUALIZE_ABOVE = 60;

interface Props {
  lines: OfferLine[];
  expanded: Set<string>;
  staged: Record<string, Change>;
  linesById: Map<string, OfferLine>;
  lineErrors: Record<string, string[]>;
  onToggle: (lineId: string) => void;
  onStage: (change: Change) => void;
  onUnstage: (lineId: string) => void;
  empty: string;
}

export function LineList(props: Props) {
  if (props.lines.length === 0) {
    return <p className="empty">{props.empty}</p>;
  }
  // The header lives outside the scroll container so it stays put.
  return (
    <>
      <LineTableHeader />
      {props.lines.length > VIRTUALIZE_ABOVE ? (
        <VirtualList {...props} />
      ) : (
        <div>
          {props.lines.map((line) => (
            <Row key={line.line_id} line={line} {...props} />
          ))}
        </div>
      )}
    </>
  );
}

function Row({
  line,
  expanded,
  staged,
  linesById,
  lineErrors,
  onToggle,
  onStage,
  onUnstage,
}: Props & { line: OfferLine }) {
  return (
    <LineRow
      line={line}
      expanded={expanded.has(line.line_id)}
      staged={staged[line.line_id]}
      errors={lineErrors[line.line_id] ?? []}
      linesById={linesById}
      onToggle={() => onToggle(line.line_id)}
      onStage={onStage}
      onUnstage={() => onUnstage(line.line_id)}
    />
  );
}

function VirtualList(props: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: props.lines.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 44,
    overscan: 10,
    getItemKey: (index) => props.lines[index].line_id,
  });

  return (
    <div ref={scrollRef} style={{ height: "68vh", overflow: "auto" }}>
      <div
        style={{
          height: virtualizer.getTotalSize(),
          position: "relative",
          width: "100%",
        }}
      >
        {virtualizer.getVirtualItems().map((item) => (
          <div
            key={item.key}
            // Expanding a row changes its height, so each one is measured
            // rather than assumed.
            ref={virtualizer.measureElement}
            data-index={item.index}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${item.start}px)`,
            }}
          >
            <Row line={props.lines[item.index]} {...props} />
          </div>
        ))}
      </div>
    </div>
  );
}
