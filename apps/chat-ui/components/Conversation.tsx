"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import {
  streamChat,
  type ChatResponse,
  type Footnote,
  type PlanTask,
} from "@/lib/api";
import { AnswerBody } from "./AnswerBody";
import { Sidenotes } from "./Sidenotes";
import { useTenant } from "./TenantContext";

type ToolProgress = {
  task_id: string;
  tool: string;
  status: "running" | "ok" | "err";
  summary?: string;
};

type Turn = {
  id: string;
  question: string;
  questionAt: Date;
  pending: boolean;
  error?: string;
  // Streaming state — populated as SSE frames arrive.
  plan?: PlanTask[];
  toolProgress?: ToolProgress[];
  composeText?: string;
  footnotes?: Footnote[];
  status?: string;
  trace_id?: string;
  response?: ChatResponse;
};

const SUGGESTED: { q: string; tag: string }[] = [
  { q: "What's my GMV for the last 7 days?", tag: "gmv · week" },
  { q: "What's my AOV for the last 30 days?", tag: "aov · month" },
  { q: "What's my RTO rate for the last 30 days?", tag: "rto · headline" },
  { q: "Show me the top 5 pincodes by RTO rate", tag: "rto · geo" },
  { q: "Show me the top 5 SKUs by RTO rate", tag: "rto · sku" },
  { q: "What's my post-RTO ROAS for the past week?", tag: "ads · roas" },
  { q: "What's my CAC for the last 14 days?", tag: "ads · cac" },
  { q: "What's my contribution margin per order this month?", tag: "margin · order" },
  { q: "Compare my RTO rate this month vs last month", tag: "rto · compare" },
  { q: "What's an industry-typical RTO rate for D2C?", tag: "refusal · test" },
];

function timestamp(d: Date): string {
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function StatusGlyph({ status }: { status: ToolProgress["status"] }) {
  // Pure CSS glyphs — keeps the visual language consistent with the rest of
  // the app (no emoji per house style).
  if (status === "running") {
    return (
      <span
        aria-label="running"
        className="font-mono"
        style={{
          display: "inline-block",
          width: 10,
          color: "var(--accent)",
          animation: "spin 1s linear infinite",
        }}
      >
        {"↻"}
      </span>
    );
  }
  if (status === "ok") {
    return (
      <span
        aria-label="done"
        className="font-mono"
        style={{ color: "var(--ok, var(--accent))" }}
      >
        {"✓"}
      </span>
    );
  }
  return (
    <span
      aria-label="error"
      className="font-mono"
      style={{ color: "var(--danger)" }}
    >
      {"✗"}
    </span>
  );
}

function PlanStrip({
  plan,
  toolProgress,
}: {
  plan: PlanTask[];
  toolProgress: ToolProgress[];
}) {
  // Merge plan with live progress so every planned task appears, even if its
  // tool_start hasn't been emitted yet (shown as "queued").
  const progressByTask = new Map<string, ToolProgress>();
  for (const tp of toolProgress) progressByTask.set(tp.task_id, tp);

  return (
    <ul
      style={{
        listStyle: "none",
        padding: 0,
        margin: "4px 0 0",
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      {plan.map((task) => {
        const tp = progressByTask.get(task.task_id);
        const queued = !tp;
        const label = tp?.summary ?? (queued ? "queued" : "running…");
        return (
          <li
            key={task.task_id}
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 8,
              fontSize: 12.5,
              color: "var(--ink-soft)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <span style={{ width: 14, textAlign: "center" }}>
              {queued ? (
                <span style={{ color: "var(--ink-dim)" }}>{"·"}</span>
              ) : (
                <StatusGlyph status={tp.status} />
              )}
            </span>
            <span style={{ color: "var(--ink)" }}>{task.tool}</span>
            <span style={{ color: "var(--ink-dim)" }}>{label}</span>
          </li>
        );
      })}
    </ul>
  );
}

function TurnBlock({ turn }: { turn: Turn }) {
  const [activeIdx, setActiveIdx] = useState<number | null>(null);

  // Prefer streamed-in footnotes while pending, fall back to the finalised
  // response.footnotes once the stream is done.
  const footnotes = useMemo<Footnote[]>(
    () => turn.response?.footnotes ?? turn.footnotes ?? [],
    [turn.response?.footnotes, turn.footnotes],
  );

  const onCiteClick = useCallback((idx: number) => {
    setActiveIdx((prev) => (prev === idx ? null : idx));
  }, []);

  const refused = turn.response?.status === "refused";
  const composing =
    turn.pending && typeof turn.composeText === "string";
  const planReceived =
    turn.pending && !composing && (turn.plan?.length ?? 0) > 0;

  return (
    <article
      className="card"
      style={{
        padding: "clamp(16px, 2vw, 24px)",
        marginBottom: 14,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <span className="badge badge-neutral">QUERY</span>
        <span className="font-mono" style={{ fontSize: 11, color: "var(--ink-dim)" }}>
          {timestamp(turn.questionAt)}
        </span>
        {turn.pending && (
          <span className="badge badge-accent" style={{ marginLeft: "auto" }}>
            <span className="dot" /> Computing
          </span>
        )}
        {turn.response && !refused && (
          <span className="badge badge-ok" style={{ marginLeft: "auto" }}>
            <span className="dot" /> {footnotes.length} sources
          </span>
        )}
        {refused && (
          <span className="badge badge-warn" style={{ marginLeft: "auto" }}>
            Refused
          </span>
        )}
        {turn.error && (
          <span className="badge badge-danger" style={{ marginLeft: "auto" }}>
            Error
          </span>
        )}
      </div>

      <div
        style={{
          marginTop: 10,
          fontSize: "1.05rem",
          fontWeight: 500,
          color: "var(--ink)",
          letterSpacing: "-0.01em",
        }}
      >
        {turn.question}
      </div>

      <div className="divider-soft" style={{ marginTop: 14, marginBottom: 14 }} />

      {turn.pending && !composing && !planReceived && (
        <div
          style={{
            fontSize: 13,
            color: "var(--ink-soft)",
          }}
        >
          Consulting the warehouse and verifying citations…
          <div className="scan-line" style={{ marginTop: 8 }} />
        </div>
      )}

      {planReceived && turn.plan && (
        <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: "var(--ink-dim)",
              marginBottom: 4,
            }}
          >
            Plan · {turn.plan.length} task{turn.plan.length === 1 ? "" : "s"}
          </div>
          <PlanStrip plan={turn.plan} toolProgress={turn.toolProgress ?? []} />
          <div className="scan-line" style={{ marginTop: 8 }} />
        </div>
      )}

      {composing && (
        <>
          <AnswerBody
            text={turn.composeText ?? ""}
            footnotes={footnotes}
            activeIdx={activeIdx}
            onCiteClick={onCiteClick}
          />
          <div className="scan-line" style={{ marginTop: 8 }} />
          {footnotes.length > 0 && (
            <Sidenotes
              footnotes={footnotes}
              activeIdx={activeIdx}
              onSelect={onCiteClick}
            />
          )}
        </>
      )}

      {turn.error && (
        <div style={{ color: "var(--danger)", fontSize: 13 }}>
          Dispatch failed: {turn.error}.
        </div>
      )}

      {refused && (
        <div style={{ color: "var(--ink-soft)", fontSize: 14 }}>
          The assistant declined to estimate without source data.
        </div>
      )}

      {turn.response && !refused && (
        <>
          <AnswerBody
            text={turn.response.text}
            footnotes={footnotes}
            activeIdx={activeIdx}
            onCiteClick={onCiteClick}
          />
          <Sidenotes
            footnotes={footnotes}
            activeIdx={activeIdx}
            onSelect={onCiteClick}
          />
        </>
      )}
    </article>
  );
}

export function Conversation() {
  const { tenantId } = useTenant();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  // Tracks the in-flight stream so a new submit cleanly cancels the previous.
  const abortRef = useRef<AbortController | null>(null);

  const submit = useCallback(
    async (text: string) => {
      const value = text.trim();
      if (!value || busy || !tenantId) return;

      // Abort any previously-running stream before starting a new one.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

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
      requestAnimationFrame(() =>
        bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }),
      );

      try {
        for await (const ev of streamChat(tenantId, value, controller.signal)) {
          setTurns((prev) =>
            prev.map((t) => {
              if (t.id !== id) return t;
              switch (ev.event) {
                case "plan":
                  return {
                    ...t,
                    plan: ev.data.tasks,
                    trace_id: ev.data.trace_id,
                  };
                case "tool_start":
                  return {
                    ...t,
                    toolProgress: [
                      ...(t.toolProgress ?? []),
                      {
                        task_id: ev.data.task_id,
                        tool: ev.data.tool,
                        status: "running",
                      },
                    ],
                  };
                case "tool_result":
                  return {
                    ...t,
                    toolProgress: (t.toolProgress ?? []).map((tp) =>
                      tp.task_id === ev.data.task_id
                        ? {
                            ...tp,
                            status: ev.data.ok ? "ok" : "err",
                            summary: ev.data.summary,
                          }
                        : tp,
                    ),
                  };
                case "compose_start":
                  return { ...t, composeText: t.composeText ?? "" };
                case "token":
                  return {
                    ...t,
                    composeText: (t.composeText ?? "") + ev.data.text,
                  };
                case "footnote":
                  return {
                    ...t,
                    footnotes: [...(t.footnotes ?? []), ev.data.footnote],
                  };
                case "done": {
                  const finalText = t.composeText ?? "";
                  const finalFootnotes = t.footnotes ?? [];
                  return {
                    ...t,
                    pending: false,
                    status: ev.data.status,
                    response: {
                      text: finalText,
                      footnotes: finalFootnotes,
                      status: ev.data.status,
                    },
                  };
                }
                case "error":
                  return {
                    ...t,
                    pending: false,
                    error: `${ev.data.code}: ${ev.data.message}`,
                  };
                case "join_decision":
                  // Joiner verdict is informational for now — surface later if
                  // we want to render a "replanning" hint inline.
                  return t;
                default: {
                  const _exhaustive: never = ev;
                  return _exhaustive;
                }
              }
            }),
          );
        }
      } catch (e: unknown) {
        // Aborts are not user-facing errors.
        if (controller.signal.aborted) return;
        const msg = e instanceof Error ? e.message : "unknown error";
        setTurns((prev) =>
          prev.map((t) =>
            t.id === id ? { ...t, pending: false, error: msg } : t,
          ),
        );
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
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
        maxWidth: 1100,
        margin: "0 auto",
        padding: "clamp(16px, 3vw, 28px) clamp(1rem, 3vw, 2rem) 9rem",
      }}
    >
      {turns.length === 0 && (
        <section
          style={{
            paddingTop: "clamp(20px, 4vw, 40px)",
            paddingBottom: 20,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <span className="badge badge-accent">
              <span className="dot pulse" /> Live
            </span>
            <span className="h-section">Cited chat</span>
          </div>
          <h1 className="h-display" style={{ margin: 0, maxWidth: "26ch" }}>
            Ask the AI Employee — every number cited.
          </h1>
          <p
            style={{
              marginTop: 8,
              color: "var(--ink-soft)",
              fontSize: 14,
              maxWidth: "60ch",
              lineHeight: 1.55,
            }}
          >
            Numbers are computed from raw connector rows in the warehouse and
            cited back to source. If the data isn&apos;t there, you&apos;ll be
            told — no estimates.
          </p>

          <div
            style={{
              marginTop: 22,
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 10,
            }}
          >
            <span className="h-section">Suggested queries</span>
            <span
              className="font-mono"
              style={{ fontSize: 11, color: "var(--ink-dim)" }}
            >
              {SUGGESTED.length}
            </span>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns:
                "repeat(auto-fill, minmax(min(260px, 100%), 1fr))",
              gap: 8,
            }}
          >
            {SUGGESTED.map((s) => (
              <button
                key={s.q}
                type="button"
                onClick={() => submit(s.q)}
                className="card"
                style={{
                  textAlign: "left",
                  cursor: "pointer",
                  padding: "12px 14px",
                  background: "var(--surface)",
                  transition: "border-color 120ms ease, background 120ms ease",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor =
                    "color-mix(in oklch, var(--accent) 50%, var(--rule))";
                  e.currentTarget.style.background = "var(--surface-2)";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = "var(--rule)";
                  e.currentTarget.style.background = "var(--surface)";
                }}
              >
                <div
                  className="font-mono"
                  style={{
                    fontSize: 10,
                    letterSpacing: "0.08em",
                    color: "var(--ink-dim)",
                    textTransform: "uppercase",
                    marginBottom: 4,
                  }}
                >
                  {s.tag}
                </div>
                <div style={{ fontSize: 14, color: "var(--ink)" }}>{s.q}</div>
              </button>
            ))}
          </div>
        </section>
      )}

      <div>
        {turns.map((t) => (
          <TurnBlock key={t.id} turn={t} />
        ))}
        <div ref={bottomRef} />
      </div>

      <div
        style={{
          position: "fixed",
          left: 0,
          right: 0,
          bottom: 0,
          background:
            "linear-gradient(to bottom, transparent, var(--bg) 30%)",
          paddingTop: 32,
          paddingBottom: 18,
          zIndex: 40,
        }}
      >
        <div
          style={{
            maxWidth: 1100,
            margin: "0 auto",
            padding: "0 clamp(1rem, 3vw, 2rem)",
          }}
        >
          <div
            className="card"
            style={{
              padding: 8,
              boxShadow:
                "0 6px 22px color-mix(in oklch, var(--ink) 10%, transparent)",
            }}
          >
            <textarea
              ref={composerRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about GMV, RTO, post-RTO ROAS, top campaigns…"
              rows={2}
              className="input"
              style={{
                border: "none",
                background: "transparent",
                padding: "8px 6px 0",
                fontSize: 14.5,
                minHeight: 52,
              }}
            />
            <div
              className="scan-line"
              style={{
                visibility: busy ? "visible" : "hidden",
                marginTop: 4,
              }}
            />
            <div
              style={{
                paddingTop: 6,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  color: "var(--ink-dim)",
                  fontSize: 11.5,
                }}
              >
                <span className="kbd">⌘</span>
                <span className="kbd">↵</span>
                <span>to dispatch</span>
              </div>
              <button
                type="button"
                onClick={() => submit(draft)}
                disabled={busy || !draft.trim()}
                className={busy || !draft.trim() ? "btn" : "btn btn-accent"}
              >
                {busy ? (
                  <>
                    <span className="dot" /> Computing…
                  </>
                ) : (
                  <>
                    Dispatch
                    <span aria-hidden>→</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
