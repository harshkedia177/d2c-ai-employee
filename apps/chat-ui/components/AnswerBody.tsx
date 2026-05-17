"use client";

import { useMemo } from "react";
import type { Footnote } from "@/lib/api";

type Props = {
  text: string;
  footnotes: Footnote[];
  activeIdx?: number | null;
  onCiteClick?: (idx: number) => void;
};

const NUMBER_RE = /(₹[\d,]+(?:\.\d+)?|\d+(?:\.\d+)?%)/g;

type Token =
  | { kind: "text"; value: string }
  | { kind: "num"; value: string; idx: number };

function tokenize(text: string): Token[] {
  const out: Token[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(NUMBER_RE)) {
    const start = m.index ?? 0;
    if (start > last) out.push({ kind: "text", value: text.slice(last, start) });
    out.push({ kind: "num", value: m[0], idx: i++ });
    last = start + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", value: text.slice(last) });
  return out;
}

function splitParagraphs(text: string): string[] {
  return text.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
}

export function AnswerBody({
  text,
  footnotes,
  activeIdx,
  onCiteClick,
}: Props) {
  const paragraphs = useMemo(() => {
    const paras = splitParagraphs(text);
    let cursor = 0;
    return paras.map((p) =>
      tokenize(p).map((t) =>
        t.kind === "num" ? { ...t, idx: cursor++ } : t,
      ),
    );
  }, [text]);

  return (
    <div
      style={{
        fontSize: "0.98rem",
        lineHeight: 1.6,
        color: "var(--ink)",
        maxWidth: "70ch",
      }}
    >
      {paragraphs.map((tokens, pi) => (
        <p key={pi} style={{ margin: pi === 0 ? 0 : "0.75rem 0 0" }}>
          {tokens.map((t, ti) => {
            if (t.kind === "text") return <span key={ti}>{t.value}</span>;
            const hasFn = t.idx < footnotes.length;
            const active = activeIdx === t.idx;
            return (
              <span key={ti} style={{ whiteSpace: "nowrap" }}>
                <span
                  className={`cite-num num ${active ? "active" : ""}`}
                  onClick={hasFn ? () => onCiteClick?.(t.idx) : undefined}
                  style={{
                    borderBottomColor: active ? "var(--accent)" : undefined,
                  }}
                >
                  {t.value}
                </span>
                {hasFn && (
                  <button
                    type="button"
                    onClick={() => onCiteClick?.(t.idx)}
                    className={`cite-chip ${active ? "active" : ""}`}
                    aria-label={`Source ${t.idx + 1}`}
                  >
                    {t.idx + 1}
                  </button>
                )}
              </span>
            );
          })}
        </p>
      ))}
    </div>
  );
}
