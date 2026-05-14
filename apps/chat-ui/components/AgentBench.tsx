"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchRuns, type AgentRun, type Citation } from "@/lib/api";
import { useTenant } from "./TenantContext";

type Filter = { id: string | null; label: string };

const FILTERS: Filter[] = [
  { id: null, label: "All" },
  { id: "rto_risk_flagger", label: "RTO Risk Flagger" },
  { id: "meta_campaign_pauser", label: "Meta Pauser" },
  { id: "pincode_cod_blocker", label: "Pincode COD Blocker" },
];

function inr(n: number | null | undefined): string {
  if (n == null) return "—";
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

function bandColor(band: string | null): string {
  if (band === "HIGH") return "var(--danger)";
  if (band === "MED") return "var(--warn)";
  return "var(--ink-soft)";
}

function bandWeight(band: string | null): number {
  if (band === "HIGH") return 700;
  if (band === "MED") return 600;
  return 400;
}

function bandStyle(band: string | null): React.CSSProperties {
  return {
    color: bandColor(band),
    fontWeight: bandWeight(band),
    fontStyle: band === "HIGH" || band === "MED" ? "normal" : "italic",
    letterSpacing: band === "HIGH" ? "0.04em" : 0,
    fontVariationSettings: '"opsz" 24, "SOFT" 0',
  };
}

function formatTriggeredAt(iso: string): string {
  const d = new Date(iso);
  const day = d.getDate();
  const months = [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
  ];
  const m = months[d.getMonth()];
  const time = d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  return `${day} ${m} ${d.getFullYear()} ${time}`;
}

function shortRunId(run_id: string): string {
  return run_id.split("-")[0];
}

type CandidateItem = Record<string, unknown>;

function candidates(run: AgentRun): CandidateItem[] {
  const ev = run.evidence ?? {};
  const c = (ev as { candidates?: unknown }).candidates;
  return Array.isArray(c) ? (c as CandidateItem[]) : [];
}

function features(run: AgentRun): [string, number][] {
  const ev = run.evidence ?? {};
  const f = (ev as { features?: Record<string, number> }).features;
  if (!f) return [];
  return Object.entries(f).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
}

function formatFeatureName(k: string): string {
  return k.replace(/_/g, " ");
}

function dotLeader(name: string, target: number): string {
  const want = Math.max(2, target - name.length);
  return "·".repeat(want);
}

function CitedList({ cits }: { cits: Citation[] }) {
  if (!cits || cits.length === 0) return null;
  const shown = cits.slice(0, 3);
  const rest = cits.length - shown.length;
  return (
    <div style={{ marginTop: "0.75rem" }}>
      <div className="eyebrow">Cited evidence</div>
      <ul style={{ listStyle: "none", padding: 0, margin: "0.25rem 0 0 0" }}>
        {shown.map((c, i) => (
          <li
            key={i}
            style={{
              fontSize: "0.78rem",
              color: "var(--ink-soft)",
              padding: "1px 0",
            }}
          >
            <span
              className="font-mono"
              style={{ color: "var(--accent)", marginRight: "0.4rem" }}
            >
              [{String.fromCharCode(97 + i)}]
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
              {c.source_id}
            </a>
            <span style={{ color: "var(--ink-soft)" }}> · {c.source_system}</span>
          </li>
        ))}
        {rest > 0 && (
          <li
            style={{
              fontSize: "0.78rem",
              color: "var(--ink-soft)",
              padding: "1px 0",
            }}
            className="font-mono"
          >
            + {rest} more
          </li>
        )}
      </ul>
    </div>
  );
}

function RtoRunBody({ run }: { run: AgentRun }) {
  const trig = (run.trigger ?? {}) as {
    order_id?: string;
    gateway?: string;
    amount_inr?: number;
    pincode?: string;
  };
  const action = (run.proposed_action ?? {}) as {
    type?: string;
    offer_pct?: number;
    channel?: string;
  };
  const feats = features(run);
  const [expanded, setExpanded] = useState(false);
  const top3 = feats.slice(0, 3);
  const rest = feats.slice(3);

  return (
    <>
      <div
        className="font-display"
        style={{ marginTop: "0.5rem", fontSize: "0.98rem" }}
      >
        Order{" "}
        <span className="font-mono" style={{ fontSize: "0.86rem" }}>
          {trig.order_id ?? "—"}
        </span>
        {trig.gateway ? ` · ${trig.gateway}` : ""}
        {trig.amount_inr ? ` · ${inr(trig.amount_inr)}` : ""}
        {trig.pincode ? ` · pincode ${trig.pincode}` : ""}
      </div>

      {run.reasoning && (
        <blockquote
          className="font-display"
          style={{
            margin: "0.9rem 0 0 0",
            padding: "0 0 0 1rem",
            borderLeft: "2px solid var(--rule)",
            fontStyle: "italic",
            fontWeight: 400,
            fontSize: "1.05rem",
            lineHeight: 1.5,
            color: "var(--ink)",
            maxWidth: "62ch",
          }}
        >
          “{run.reasoning}”
        </blockquote>
      )}

      <div style={{ marginTop: "0.9rem" }}>
        <div className="eyebrow">Would do</div>
        <div className="font-display" style={{ fontSize: "0.98rem", marginTop: "2px" }}>
          Proposed action:{" "}
          <span style={{ fontWeight: 500 }}>
            {action.type ?? "—"}
          </span>
          {action.offer_pct ? ` · offer ${action.offer_pct}% prepaid switch` : ""}
          {action.channel ? ` · via ${action.channel}` : ""}
        </div>
      </div>

      {feats.length > 0 && (
        <div style={{ marginTop: "0.9rem" }}>
          <div className="eyebrow">Features</div>
          <table
            style={{
              borderCollapse: "collapse",
              marginTop: "4px",
              fontSize: "0.86rem",
            }}
          >
            <tbody>
              {(expanded ? feats : top3).map(([k, v]) => (
                <tr key={k}>
                  <td
                    style={{ paddingRight: "0.6rem", color: "var(--ink-soft)" }}
                    className="font-mono"
                  >
                    {formatFeatureName(k)}
                  </td>
                  <td
                    style={{
                      color: "var(--ink-soft)",
                      fontFamily: "var(--font-geist-mono)",
                      letterSpacing: "0.02em",
                    }}
                  >
                    {dotLeader(formatFeatureName(k), 28)}
                  </td>
                  <td
                    className="numeral"
                    style={{
                      paddingLeft: "0.4rem",
                      fontVariantNumeric: "tabular-nums",
                      color: "var(--ink)",
                    }}
                  >
                    {v.toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rest.length > 0 && (
            <button
              type="button"
              onClick={() => setExpanded((x) => !x)}
              className="font-mono"
              style={{
                marginTop: "4px",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                color: "var(--ink)",
                fontSize: "0.72rem",
                letterSpacing: "0.06em",
                padding: 0,
              }}
            >
              {expanded ? "collapse" : `view all ${feats.length} features ⟶`}
            </button>
          )}
        </div>
      )}

      <CitedList cits={run.cited_provenance ?? []} />
    </>
  );
}

function MetaPauserBody({ run }: { run: AgentRun }) {
  const cands = candidates(run) as Array<{
    campaign_id?: string;
    name?: string;
    spend_inr?: number;
    raw_roas?: number;
    post_rto_roas?: number;
    rto_rate?: number;
  }>;
  const [expanded, setExpanded] = useState(true);

  return (
    <>
      {run.reasoning && (
        <blockquote
          className="font-display"
          style={{
            margin: "0.6rem 0 0 0",
            padding: "0 0 0 1rem",
            borderLeft: "2px solid var(--rule)",
            fontStyle: "italic",
            fontSize: "1.05rem",
            lineHeight: 1.5,
            maxWidth: "62ch",
          }}
        >
          “{run.reasoning}”
        </blockquote>
      )}

      <div style={{ marginTop: "0.7rem" }}>
        <div className="eyebrow">Summary</div>
        <div className="font-display" style={{ fontSize: "0.98rem", marginTop: 2 }}>
          {cands.length} campaign{cands.length === 1 ? "" : "s"} flagged · expected
          savings {inr(run.expected_savings_inr)}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="font-mono"
          style={{
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--ink)",
            fontSize: "0.72rem",
            letterSpacing: "0.06em",
            padding: "2px 0 0 0",
          }}
        >
          {expanded ? "collapse proposals" : "view all proposals ⟶"}
        </button>
      </div>

      {expanded && (
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: "0.6rem 0 0 0",
            borderTop: "1px solid var(--rule)",
          }}
        >
          {cands.map((c, i) => (
            <li
              key={i}
              style={{
                padding: "0.5rem 0",
                borderBottom: "1px solid var(--rule)",
                display: "grid",
                gridTemplateColumns: "1fr auto",
                alignItems: "baseline",
                gap: "1rem",
              }}
            >
              <div>
                <div className="font-display" style={{ fontSize: "0.98rem" }}>
                  {c.name ?? c.campaign_id}
                </div>
                <div
                  className="font-mono"
                  style={{ fontSize: "0.72rem", color: "var(--ink-soft)" }}
                >
                  {c.campaign_id} · spend {inr(c.spend_inr)} · raw ROAS{" "}
                  {c.raw_roas?.toFixed(2)} · post-RTO ROAS{" "}
                  {c.post_rto_roas?.toFixed(2)} · RTO{" "}
                  {((c.rto_rate ?? 0) * 100).toFixed(1)}%
                </div>
              </div>
              <div
                className="numeral"
                style={{
                  color: (c.post_rto_roas ?? 1) < 1 ? "var(--danger)" : "var(--ink)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                pause
              </div>
            </li>
          ))}
        </ul>
      )}

      <CitedList cits={run.cited_provenance ?? []} />
    </>
  );
}

function PincodeBlockerBody({ run }: { run: AgentRun }) {
  const cands = candidates(run) as Array<{
    pincode?: string;
    rto_rate?: number;
    orders?: number;
    expected_loss_inr?: number;
  }>;
  const [expanded, setExpanded] = useState(true);

  return (
    <>
      {run.reasoning && (
        <blockquote
          className="font-display"
          style={{
            margin: "0.6rem 0 0 0",
            padding: "0 0 0 1rem",
            borderLeft: "2px solid var(--rule)",
            fontStyle: "italic",
            fontSize: "1.05rem",
            maxWidth: "62ch",
          }}
        >
          “{run.reasoning}”
        </blockquote>
      )}

      <div style={{ marginTop: "0.7rem" }}>
        <div className="eyebrow">Summary</div>
        <div className="font-display" style={{ fontSize: "0.98rem", marginTop: 2 }}>
          {cands.length} pincode{cands.length === 1 ? "" : "s"} flagged · expected
          savings {inr(run.expected_savings_inr)}
        </div>
        <button
          type="button"
          onClick={() => setExpanded((x) => !x)}
          className="font-mono"
          style={{
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--ink)",
            fontSize: "0.72rem",
            letterSpacing: "0.06em",
            padding: "2px 0 0 0",
          }}
        >
          {expanded ? "collapse proposals" : "view all proposals ⟶"}
        </button>
      </div>

      {expanded && (
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: "0.6rem 0 0 0",
            borderTop: "1px solid var(--rule)",
          }}
        >
          {cands.map((c, i) => (
            <li
              key={i}
              style={{
                padding: "0.5rem 0",
                borderBottom: "1px solid var(--rule)",
                display: "grid",
                gridTemplateColumns: "1fr auto",
                alignItems: "baseline",
                gap: "1rem",
              }}
            >
              <div>
                <div className="font-display" style={{ fontSize: "0.98rem" }}>
                  Pincode <span className="font-mono">{c.pincode}</span>
                </div>
                <div
                  className="font-mono"
                  style={{ fontSize: "0.72rem", color: "var(--ink-soft)" }}
                >
                  RTO {((c.rto_rate ?? 0) * 100).toFixed(1)}% · {c.orders} orders · expected loss{" "}
                  {inr(c.expected_loss_inr)}
                </div>
              </div>
              <div
                className="numeral"
                style={{ color: "var(--danger)" }}
              >
                block COD
              </div>
            </li>
          ))}
        </ul>
      )}

      <CitedList cits={run.cited_provenance ?? []} />
    </>
  );
}

function RunBlock({ run }: { run: AgentRun }) {
  let body: React.ReactNode = null;
  if (run.agent_id === "meta_campaign_pauser") {
    body = <MetaPauserBody run={run} />;
  } else if (run.agent_id === "pincode_cod_blocker") {
    body = <PincodeBlockerBody run={run} />;
  } else {
    body = <RtoRunBody run={run} />;
  }

  return (
    <article
      style={{
        borderTop: "1px solid var(--rule)",
        padding: "1.5rem 0",
      }}
    >
      <div
        className="font-mono"
        style={{
          fontSize: "0.7rem",
          letterSpacing: "0.14em",
          color: "var(--ink-soft)",
          textTransform: "uppercase",
        }}
      >
        <span style={{ color: "var(--ink)" }}>{run.agent_id}</span>
        <span style={{ margin: "0 0.5rem" }}>·</span>
        Run #{shortRunId(run.run_id)}
        <span style={{ margin: "0 0.5rem" }}>·</span>
        {formatTriggeredAt(run.triggered_at)}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr auto",
          gap: "1.5rem",
          alignItems: "baseline",
          marginTop: "0.6rem",
        }}
      >
        <div className="headline" style={bandStyle(run.band)}>
          <span style={{ fontSize: "1.5rem" }}>{run.band ?? "—"}</span>
          {run.score != null && (
            <span
              className="font-mono"
              style={{
                marginLeft: "0.6rem",
                fontSize: "0.78rem",
                letterSpacing: "0.04em",
                color: "var(--ink-soft)",
              }}
            >
              score {run.score.toFixed(2)}
            </span>
          )}
        </div>

        <div
          className="numeral"
          style={{
            color: run.band === "LOW" ? "var(--ink-soft)" : "var(--ink)",
            fontSize: "1.4rem",
            fontVariantNumeric: "tabular-nums",
            textAlign: "right",
          }}
        >
          {inr(run.expected_savings_inr)}
          <div className="eyebrow" style={{ marginTop: 2 }}>
            expected savings
          </div>
        </div>
      </div>

      {body}
    </article>
  );
}

export function AgentBench() {
  const { tenantId } = useTenant();
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filterIdx, setFilterIdx] = useState(0);

  const load = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    setError(null);
    try {
      const f = FILTERS[filterIdx];
      const rows = await fetchRuns(tenantId, f.id ?? undefined);
      setRuns(rows);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "fetch failed");
    } finally {
      setLoading(false);
    }
  }, [tenantId, filterIdx]);

  useEffect(() => {
    void load();
  }, [load]);

  const tenantShort = useMemo(
    () => (tenantId ? `${tenantId.slice(0, 8)}…${tenantId.slice(-4)}` : ""),
    [tenantId],
  );

  return (
    <main
      style={{
        marginLeft: "clamp(2rem, 8vw, 10rem)",
        marginRight: "clamp(2rem, 5vw, 5rem)",
        maxWidth: "1400px",
        paddingTop: "clamp(1.5rem, 3vw, 2.5rem)",
        paddingBottom: "6rem",
      }}
    >
      <div
        className="headline"
        style={{ fontSize: "clamp(2rem, 4vw, 3.5rem)" }}
      >
        Agent Bench
      </div>
      <div className="eyebrow" style={{ marginTop: "0.4rem" }}>
        Proposed actions · never executed · tenant{" "}
        <span style={{ color: "var(--ink)" }}>{tenantShort}</span>
      </div>

      <div
        style={{
          marginTop: "1.5rem",
          paddingTop: "0.6rem",
          paddingBottom: "0.6rem",
          borderTop: "1px solid var(--rule)",
          borderBottom: "1px solid var(--rule)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "0.5rem",
        }}
      >
        <div
          className="font-mono"
          style={{ fontSize: "0.7rem", letterSpacing: "0.14em" }}
        >
          {FILTERS.map((f, i) => (
            <span key={f.label}>
              {i > 0 && (
                <span style={{ color: "var(--ink-soft)", margin: "0 0.5rem" }}>·</span>
              )}
              <button
                type="button"
                onClick={() => setFilterIdx(i)}
                style={{
                  background: "transparent",
                  border: "none",
                  cursor: "pointer",
                  padding: 0,
                  textTransform: "uppercase",
                  letterSpacing: "0.14em",
                  fontSize: "0.7rem",
                  color: i === filterIdx ? "var(--ink)" : "var(--ink-soft)",
                  textDecoration: i === filterIdx ? "underline" : "none",
                  textDecorationColor: "var(--accent)",
                  textUnderlineOffset: "5px",
                  textDecorationThickness: "1px",
                  fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
                }}
              >
                {f.label}
              </button>
            </span>
          ))}
        </div>

        <button
          type="button"
          onClick={() => void load()}
          className="font-mono"
          style={{
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--ink)",
            fontSize: "0.7rem",
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            padding: 0,
          }}
        >
          ⟳ {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div
          className="font-display"
          style={{
            fontStyle: "italic",
            color: "var(--danger)",
            marginTop: "1.5rem",
          }}
        >
          Could not load runs: {error}.
        </div>
      )}

      {!error && runs.length === 0 && !loading && (
        <div
          style={{
            marginTop: "1.5rem",
            borderTop: "1px solid var(--rule)",
            borderBottom: "1px solid var(--rule)",
            padding: "2rem 0",
            maxWidth: "60ch",
          }}
        >
          <div
            className="font-display"
            style={{
              fontStyle: "italic",
              fontSize: "1.1rem",
              lineHeight: 1.5,
              color: "var(--ink)",
            }}
          >
            The bench is quiet. No agent has proposed anything in this window —
            usually a good sign, occasionally a sign your webhooks aren&apos;t firing.
          </div>
        </div>
      )}

      <div style={{ marginTop: "0.5rem" }}>
        {runs.map((r) => (
          <RunBlock key={r.run_id} run={r} />
        ))}
      </div>
    </main>
  );
}
