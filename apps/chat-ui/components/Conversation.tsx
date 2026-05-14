"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  postChat,
  type ChatResponse,
  type Footnote,
} from "@/lib/api";
import { AnswerBody } from "./AnswerBody";
import { Sidenotes } from "./Sidenotes";
import { useTenant } from "./TenantContext";

type Turn = {
  id: string;
  question: string;
  questionAt: Date;
  pending: boolean;
  error?: string;
  response?: ChatResponse;
};

const SUGGESTED: [string, string][] = [
  ["What's my GMV last 30 days?", "What's my AOV by month?"],
  ["What's my RTO rate?", "Top campaigns by post-RTO ROAS"],
  ["Worst pincodes by RTO this quarter", "Show me COD orders today"],
];

function timestamp(d: Date): string {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

type TurnBlockProps = {
  turn: Turn;
  isMobile: boolean;
};

function TurnBlock({ turn, isMobile }: TurnBlockProps) {
  const blockRef = useRef<HTMLDivElement | null>(null);
  const [positions, setPositions] = useState<{ idx: number; top: number; id: string }[]>([]);
  const [activeIdx, setActiveIdx] = useState<number | null>(null);

  const footnotes = useMemo<Footnote[]>(
    () => turn.response?.footnotes ?? [],
    [turn.response?.footnotes],
  );

  const onCiteClick = useCallback((idx: number) => {
    setActiveIdx((prev) => (prev === idx ? null : idx));
  }, []);

  const refused = turn.response?.status === "refused";

  return (
    <div
      ref={blockRef}
      style={{
        borderTop: "1px solid var(--rule)",
        paddingTop: "1.25rem",
        paddingBottom: "2rem",
        position: "relative",
      }}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: isMobile ? "1fr" : "minmax(0, 1fr) minmax(0, 28ch)",
          gap: isMobile ? "1rem" : "clamp(2rem, 4vw, 5rem)",
          alignItems: "start",
        }}
      >
        <div>
          <div
            className="eyebrow"
            style={{ display: "flex", justifyContent: "space-between" }}
          >
            <span>Query · {timestamp(turn.questionAt)}</span>
          </div>
          <div className="editorial-question" style={{ marginTop: "0.5rem" }}>
            {turn.question}
          </div>

          <div style={{ marginTop: "1.25rem" }} className="eyebrow">
            Answer
          </div>

          {turn.pending && (
            <div
              className="font-display"
              style={{
                fontStyle: "italic",
                fontSize: "1.05rem",
                color: "var(--ink-soft)",
                marginTop: "0.5rem",
              }}
            >
              Computing — consulting the warehouse and verifying citations…
            </div>
          )}

          {turn.error && (
            <div
              className="font-display"
              style={{
                fontStyle: "italic",
                color: "var(--danger)",
                marginTop: "0.5rem",
              }}
            >
              The dispatch failed: {turn.error}.
            </div>
          )}

          {refused && (
            <div
              className="font-display"
              style={{
                fontStyle: "italic",
                color: "var(--ink-soft)",
                marginTop: "0.5rem",
              }}
            >
              The assistant declined to estimate without source data.
            </div>
          )}

          {turn.response && !refused && (
            <div style={{ marginTop: "0.5rem" }}>
              <AnswerBody
                containerId={turn.id}
                text={turn.response.text}
                footnotes={footnotes}
                onCiteAlignmentChange={setPositions}
                activeIdx={activeIdx}
                onCiteClick={onCiteClick}
              />

              {isMobile && footnotes.length > 0 && (
                <div style={{ marginTop: "1rem" }}>
                  {footnotes.map((fn, i) => (
                    <details
                      key={i}
                      style={{
                        borderTop: "1px solid var(--rule)",
                        padding: "0.5rem 0",
                      }}
                    >
                      <summary
                        className="font-mono"
                        style={{
                          cursor: "pointer",
                          fontSize: "0.74rem",
                          letterSpacing: "0.05em",
                          color: "var(--accent)",
                        }}
                      >
                        [{i + 1}] {fn.citations?.[0]?.source_system ?? "source"}
                        {fn.total_sources
                          ? ` · ${fn.total_sources.toLocaleString()} rows`
                          : ""}
                      </summary>
                      <ul style={{ listStyle: "none", padding: "6px 0 0 0" }}>
                        {(fn.citations ?? []).slice(0, 5).map((c, j) => (
                          <li
                            key={j}
                            style={{
                              fontSize: "0.74rem",
                              paddingLeft: "0.9rem",
                              position: "relative",
                              color: "var(--ink-soft)",
                            }}
                          >
                            <span style={{ position: "absolute", left: 0 }}>–</span>
                            <a
                              href={c.url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-mono"
                              style={{ color: "var(--ink)" }}
                            >
                              {c.url}
                            </a>
                          </li>
                        ))}
                      </ul>
                    </details>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {!isMobile && turn.response && footnotes.length > 0 && (
          <div style={{ position: "relative", minHeight: 1 }}>
            <Sidenotes
              footnotes={footnotes}
              positions={positions}
              activeIdx={activeIdx}
              onSelect={onCiteClick}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function Conversation() {
  const { tenantId } = useTenant();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 900);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  const submit = useCallback(
    async (text: string) => {
      const value = text.trim();
      if (!value || busy || !tenantId) return;
      const id = `t-${Date.now()}`;
      const newTurn: Turn = {
        id,
        question: value,
        questionAt: new Date(),
        pending: true,
      };
      setTurns((prev) => [...prev, newTurn]);
      setDraft("");
      setBusy(true);
      try {
        const r = await postChat(tenantId, value);
        setTurns((prev) =>
          prev.map((t) =>
            t.id === id ? { ...t, pending: false, response: r } : t,
          ),
        );
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "unknown error";
        setTurns((prev) =>
          prev.map((t) =>
            t.id === id ? { ...t, pending: false, error: msg } : t,
          ),
        );
      } finally {
        setBusy(false);
      }
    },
    [busy, tenantId],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void submit(draft);
    }
  };

  return (
    <main
      style={{
        marginLeft: "clamp(2rem, 8vw, 10rem)",
        marginRight: "clamp(2rem, 5vw, 5rem)",
        maxWidth: "1400px",
        paddingTop: "clamp(1.5rem, 3vw, 2.5rem)",
        paddingBottom: "8rem",
      }}
    >
      {turns.length === 0 && (
        <section style={{ paddingBottom: "2rem" }}>
          <div
            className="headline"
            style={{
              fontSize: "clamp(2rem, 4vw, 3rem)",
              maxWidth: "30ch",
              marginBottom: "0.5rem",
            }}
          >
            Ask the AI Employee.
          </div>
          <div
            className="font-display"
            style={{
              fontStyle: "italic",
              fontSize: "1.05rem",
              color: "var(--ink-soft)",
              marginBottom: "1.5rem",
              maxWidth: "55ch",
            }}
          >
            Every number is computed from raw connector rows and cited back to a
            source. Refusals are honest — if the data isn&apos;t there, you&apos;ll be told.
          </div>

          <div className="eyebrow" style={{ marginBottom: "0.75rem" }}>
            Suggested queries
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(20ch, 1fr))",
              rowGap: "0.5rem",
              columnGap: "2rem",
              maxWidth: "78ch",
              borderTop: "1px solid var(--rule)",
              paddingTop: "0.75rem",
            }}
          >
            {SUGGESTED.flat().map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => submit(p)}
                className="prompt-link font-display"
                style={{
                  textAlign: "left",
                  background: "transparent",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                  fontSize: "1rem",
                  fontWeight: 400,
                }}
              >
                {p}
              </button>
            ))}
          </div>
        </section>
      )}

      <div>
        {turns.map((t) => (
          <TurnBlock key={t.id} turn={t} isMobile={isMobile} />
        ))}
      </div>

      <div
        style={{
          position: "sticky",
          bottom: 0,
          background: "var(--paper)",
          paddingTop: "1rem",
          paddingBottom: "1rem",
          borderTop: "2px solid var(--ink)",
          marginTop: "2rem",
        }}
      >
        <textarea
          ref={composerRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about GMV, RTO, post-RTO ROAS, top campaigns…"
          rows={2}
          style={{
            width: "100%",
            background: "transparent",
            border: "none",
            outline: "none",
            resize: "none",
            fontFamily: "var(--font-fraunces), Georgia, serif",
            fontVariationSettings: '"opsz" 16, "SOFT" 50',
            fontSize: "1.05rem",
            lineHeight: 1.5,
            color: "var(--ink)",
            padding: "0.5rem 0",
          }}
        />
        <div className="scan-line" style={{ visibility: busy ? "visible" : "hidden" }} />
        <div
          style={{
            borderTop: "1px solid var(--rule)",
            paddingTop: "0.5rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div className="eyebrow">⌘+Enter to dispatch</div>
          <button
            type="button"
            onClick={() => submit(draft)}
            disabled={busy || !draft.trim()}
            className="font-mono"
            style={{
              background: "transparent",
              border: "none",
              cursor: busy || !draft.trim() ? "default" : "pointer",
              color: busy ? "var(--accent)" : "var(--ink)",
              fontSize: "0.72rem",
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              opacity: !draft.trim() && !busy ? 0.4 : 1,
            }}
            onMouseEnter={(e) => {
              if (!busy && draft.trim()) e.currentTarget.style.color = "var(--accent)";
            }}
            onMouseLeave={(e) => {
              if (!busy) e.currentTarget.style.color = "var(--ink)";
            }}
          >
            {busy ? "… Computing" : "Dispatch ⟶"}
          </button>
        </div>
      </div>
    </main>
  );
}
