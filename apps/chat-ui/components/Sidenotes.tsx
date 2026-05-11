"use client";

import type { Footnote } from "@/lib/api";

type Props = {
  footnotes: Footnote[];
  positions: { idx: number; top: number; id: string }[];
  activeIdx: number | null;
  onSelect?: (idx: number) => void;
};

function truncMiddle(s: string, head = 18, tail = 8): string {
  if (s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

export function Sidenotes({ footnotes, positions, activeIdx, onSelect }: Props) {
  return (
    <div
      style={{
        position: "relative",
        minHeight: "100%",
      }}
    >
      {footnotes.map((fn, idx) => {
        const pos = positions.find((p) => p.idx === idx);
        const active = activeIdx === idx;
        const cits = fn.citations ?? [];
        const top = pos?.top ?? idx * 120;
        return (
          <div
            key={idx}
            id={pos?.id ?? `cite-${idx}`}
            className={`sidenote ${active ? "active" : ""}`}
            onClick={() => onSelect?.(idx)}
            style={{
              position: "absolute",
              top: `${top}px`,
              left: 0,
              right: 0,
              cursor: "pointer",
              transition: "top 220ms ease",
            }}
          >
            <div
              className="font-mono"
              style={{
                fontSize: "0.72rem",
                color: "var(--accent)",
                letterSpacing: "0.04em",
              }}
            >
              [{idx + 1}]
            </div>
            <div className="eyebrow" style={{ marginTop: 2 }}>
              {cits[0]?.source_system ?? "—"}
              {fn.metric_id ? ` · ${fn.metric_id}` : ""}
            </div>
            {(fn.total_sources != null || fn.sample_size != null) && (
              <div
                className="font-display"
                style={{
                  fontStyle: "italic",
                  fontSize: "0.88rem",
                  color: "var(--ink-soft)",
                  marginTop: 4,
                }}
              >
                {fn.total_sources != null
                  ? `${fn.total_sources.toLocaleString()} rows`
                  : ""}
                {fn.sample_size != null
                  ? ` · sample ${fn.sample_size.toLocaleString()}`
                  : ""}
              </div>
            )}
            {cits.length > 0 && (
              <details
                style={{ marginTop: 6 }}
                open={active}
              >
                <summary
                  className="font-mono"
                  style={{
                    fontSize: "0.7rem",
                    color: "var(--ink)",
                    cursor: "pointer",
                    letterSpacing: "0.06em",
                    listStyle: "none",
                  }}
                >
                  View {Math.min(5, cits.length)} of {cits.length} ⟶
                </summary>
                <ul
                  style={{
                    listStyle: "none",
                    padding: 0,
                    margin: "6px 0 0 0",
                  }}
                >
                  {cits.slice(0, 5).map((c, i) => (
                    <li
                      key={i}
                      style={{
                        fontSize: "0.74rem",
                        color: "var(--ink-soft)",
                        lineHeight: 1.45,
                        paddingLeft: "0.8rem",
                        position: "relative",
                      }}
                    >
                      <span
                        style={{
                          position: "absolute",
                          left: 0,
                          color: "var(--ink-soft)",
                        }}
                      >
                        –
                      </span>
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono"
                        style={{
                          color: "var(--ink)",
                          textDecoration: "none",
                          borderBottom: "1px dotted var(--rule)",
                        }}
                      >
                        {truncMiddle(c.url)}
                      </a>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}
