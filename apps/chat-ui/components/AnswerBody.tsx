"use client";

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { Footnote } from "@/lib/api";

type Props = {
  text: string;
  footnotes: Footnote[];
  onCiteAlignmentChange?: (
    positions: { idx: number; top: number; id: string }[],
  ) => void;
  activeIdx?: number | null;
  onCiteClick?: (idx: number) => void;
  containerId: string;
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
    if (start > last) {
      out.push({ kind: "text", value: text.slice(last, start) });
    }
    out.push({ kind: "num", value: m[0], idx: i++ });
    last = start + m[0].length;
  }
  if (last < text.length) {
    out.push({ kind: "text", value: text.slice(last) });
  }
  return out;
}

function splitParagraphs(text: string): string[] {
  return text
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
}

export function AnswerBody({
  text,
  footnotes,
  onCiteAlignmentChange,
  activeIdx,
  onCiteClick,
  containerId,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const marksRef = useRef<Map<number, HTMLElement>>(new Map());
  const [, setTick] = useState(0);

  const paragraphs = useMemo(() => {
    const paras = splitParagraphs(text);
    let cursor = 0;
    return paras.map((p) => {
      const toks = tokenize(p).map((t) => {
        if (t.kind === "num") return { ...t, idx: cursor++ };
        return t;
      });
      return toks;
    });
  }, [text]);

  const numMarks = useMemo(
    () => paragraphs.flat().filter((t) => t.kind === "num").length,
    [paragraphs],
  );

  const reportAlignment = () => {
    if (!onCiteAlignmentChange) return;
    const cont = containerRef.current;
    if (!cont) return;
    const cBox = cont.getBoundingClientRect();
    const out: { idx: number; top: number; id: string }[] = [];
    for (let i = 0; i < numMarks; i++) {
      const m = marksRef.current.get(i);
      if (!m) continue;
      const r = m.getBoundingClientRect();
      out.push({
        idx: i,
        top: r.top - cBox.top,
        id: `${containerId}-cite-${i}`,
      });
    }
    onCiteAlignmentChange(out);
  };

  useLayoutEffect(() => {
    reportAlignment();
    // re-measure once after first paint
    const id = window.requestAnimationFrame(reportAlignment);
    return () => window.cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, numMarks]);

  useEffect(() => {
    const onResize = () => {
      setTick((x) => x + 1);
      reportAlignment();
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      ref={containerRef}
      className="editorial-body has-dropcap"
      style={{ position: "relative" }}
    >
      {paragraphs.map((tokens, pi) => (
        <p key={pi}>
          {tokens.map((t, ti) => {
            if (t.kind === "text") return <span key={ti}>{t.value}</span>;
            const hasFn = t.idx < footnotes.length;
            const id = `${containerId}-mark-${t.idx}`;
            return (
              <mark
                key={ti}
                id={id}
                className={`cite numeral ${activeIdx === t.idx ? "active" : ""}`}
                ref={(el) => {
                  if (el) marksRef.current.set(t.idx, el);
                  else marksRef.current.delete(t.idx);
                }}
                data-fn={t.idx}
                onClick={() => onCiteClick?.(t.idx)}
                title={hasFn ? `Footnote [${t.idx + 1}]` : "No source"}
              >
                {t.value}
                {hasFn && <sup>[{t.idx + 1}]</sup>}
              </mark>
            );
          })}
        </p>
      ))}
    </div>
  );
}
