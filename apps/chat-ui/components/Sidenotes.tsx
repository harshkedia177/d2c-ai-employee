"use client";

import { useState } from "react";
import type { Citation, Footnote } from "@/lib/api";

type Props = {
  footnotes: Footnote[];
  activeIdx: number | null;
  onSelect?: (idx: number) => void;
};

/** Human-friendly noun for the unit of provenance, per source system. */
function unitFor(systemRaw: string | undefined): string {
  const s = (systemRaw ?? "").toLowerCase();
  if (s.includes("shopify")) return "Shopify orders";
  if (s.includes("shiprocket")) return "Shiprocket shipments";
  if (s.includes("meta") || s.includes("facebook"))
    return "Meta ad records";
  if (s.includes("google")) return "Google Ads records";
  return "source rows";
}

function provenanceLine(
  fn: Footnote,
  system: string | undefined,
): string {
  const unit = unitFor(system);
  const total = fn.total_sources;
  const sample = fn.sample_size;
  if (total == null && sample == null) return "";
  // Common case: backend sets both to the same count for small datasets.
  if (total != null && (sample == null || sample === total)) {
    return `Computed from ${total.toLocaleString()} ${unit}`;
  }
  if (sample != null && total != null && sample < total) {
    return `Sampled ${sample.toLocaleString()} of ${total.toLocaleString()} ${unit}`;
  }
  if (sample != null) {
    return `Computed from ${sample.toLocaleString()} ${unit}`;
  }
  return "";
}

function FootnoteCard({
  fn,
  idx,
  active,
  onSelect,
}: {
  fn: Footnote;
  idx: number;
  active: boolean;
  onSelect?: (idx: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const cits: Citation[] = fn.citations ?? [];
  const system = cits[0]?.source_system;
  const provenance = provenanceLine(fn, system);

  return (
    <div
      className="card"
      onClick={() => onSelect?.(idx)}
      style={{
        padding: 12,
        cursor: "pointer",
        outline: active
          ? "2px solid var(--accent)"
          : "2px solid transparent",
        outlineOffset: -1,
        transition: "outline-color 120ms ease",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 6,
          flexWrap: "wrap",
        }}
      >
        <span className="badge badge-accent">[{idx + 1}]</span>
        <span
          className="font-mono"
          style={{
            fontSize: 11,
            color: "var(--ink-soft)",
            letterSpacing: "0.04em",
            textTransform: "uppercase",
          }}
        >
          {system ?? "—"}
        </span>
        {fn.metric_id && (
          <span className="badge badge-neutral">{fn.metric_id}</span>
        )}
      </div>

      {provenance && (
        <div
          style={{
            fontSize: 13,
            color: "var(--ink)",
            lineHeight: 1.5,
            marginBottom: cits.length > 0 ? 8 : 0,
          }}
        >
          {provenance}
        </div>
      )}

      {cits.length > 0 && (
        <>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setOpen((x) => !x);
            }}
            style={{
              background: "transparent",
              border: "none",
              padding: 0,
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              letterSpacing: "0.04em",
              color: "var(--accent)",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <span aria-hidden style={{ fontSize: 9 }}>
              {open ? "▾" : "▸"}
            </span>
            {open
              ? "Hide source row IDs"
              : `View ${cits.length} source row ID${cits.length === 1 ? "" : "s"}`}
          </button>

          {open && (
            <ul
              style={{
                listStyle: "none",
                padding: 0,
                margin: "8px 0 0",
                display: "flex",
                flexDirection: "column",
                gap: 3,
                borderTop: "1px solid var(--rule-soft)",
                paddingTop: 8,
                maxHeight: 200,
                overflowY: "auto",
              }}
            >
              {cits.map((c, i) => (
                <li
                  key={i}
                  style={{
                    fontSize: 11.5,
                    color: "var(--ink-soft)",
                    display: "flex",
                    alignItems: "baseline",
                    gap: 6,
                  }}
                >
                  <span
                    className="font-mono"
                    style={{ color: "var(--ink-dim)", fontSize: 10, width: 22 }}
                  >
                    {String(i + 1).padStart(3, "0")}
                  </span>
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono"
                    onClick={(e) => e.stopPropagation()}
                    title={c.url}
                    style={{
                      color: "var(--ink)",
                      borderBottom: "1px dotted var(--rule)",
                    }}
                  >
                    {c.source_id || c.url}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

export function Sidenotes({ footnotes, activeIdx, onSelect }: Props) {
  if (footnotes.length === 0) return null;
  return (
    <section style={{ marginTop: 20 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 10,
        }}
      >
        <span className="h-section">Sources</span>
        <span
          className="font-mono"
          style={{ fontSize: 11, color: "var(--ink-dim)" }}
        >
          {footnotes.length}
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            "repeat(auto-fill, minmax(min(280px, 100%), 1fr))",
          gap: 10,
        }}
      >
        {footnotes.map((fn, idx) => (
          <FootnoteCard
            key={idx}
            fn={fn}
            idx={idx}
            active={activeIdx === idx}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}
