"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchRuns,
  triggerCron,
  triggerWebhook,
  type AgentRun,
  type Citation,
} from "@/lib/api";
import { useTenant } from "./TenantContext";

/* ──────────────────────────────────────────────────────────────────
   Defensive helpers — backend evidence shapes drift across agents.
   Read once, narrow safely.
   ────────────────────────────────────────────────────────────────── */

function asNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}
function asString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}
function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}

function inr(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return "₹" + Math.round(n).toLocaleString("en-IN");
}
function pct(n: number | null | undefined, digits = 1): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}
function num(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}
function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString([], {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
function shortRunId(run_id: string): string {
  return run_id.split("-")[0];
}

/* Cheap content hash for dedupe — agents that fire on a cron repeatedly
   produce byte-identical re-evaluations. */
function runFingerprint(r: AgentRun): string {
  return [
    r.agent_id,
    r.band,
    r.expected_savings_inr ?? 0,
    (r.reasoning ?? "").length,
    (r.reasoning ?? "").slice(0, 80),
  ].join("|");
}

/* ── Agent registry ──────────────────────────────────────────────── */

type AgentMeta = {
  id: string;
  label: string;
  hint: string;
};

const AGENTS: AgentMeta[] = [
  {
    id: "rto_risk_flagger",
    label: "RTO Risk Flagger",
    hint: "Per-order webhook · scores RTO risk",
  },
  {
    id: "meta_pauser",
    label: "Meta Pauser",
    hint: "Scheduled · pauses negative-ROAS campaigns",
  },
  {
    id: "pincode_cod_blocker",
    label: "Pincode COD Blocker",
    hint: "Scheduled · blocks high-RTO pincodes",
  },
];
const AGENT_BY_ID = Object.fromEntries(AGENTS.map((a) => [a.id, a]));
function agentLabel(id: string): string {
  return AGENT_BY_ID[id]?.label ?? id;
}
function bandPillClass(band: string | null): string {
  if (band === "HIGH") return "badge badge-danger";
  if (band === "MED") return "badge badge-warn";
  if (band === "LOW") return "badge badge-ok";
  return "badge badge-neutral";
}

/* ── Cited evidence list ─────────────────────────────────────────── */

function CitedList({ cits }: { cits: Citation[] }) {
  if (!cits || cits.length === 0) return null;
  return (
    <div style={{ marginTop: 14 }}>
      <div className="h-section" style={{ marginBottom: 6 }}>
        Cited evidence
      </div>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {cits.map((c, i) => (
          <li
            key={i}
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 8,
              fontSize: 12,
              color: "var(--ink-soft)",
            }}
          >
            <span className="badge badge-neutral">
              {String.fromCharCode(97 + i)}
            </span>
            <span
              className="font-mono"
              style={{
                fontSize: 11,
                color: "var(--ink-dim)",
                letterSpacing: "0.04em",
                textTransform: "uppercase",
              }}
            >
              {c.source_system}
            </span>
            <a
              href={c.url}
              target="_blank"
              rel="noreferrer"
              className="font-mono"
              style={{
                color: "var(--ink)",
                fontSize: 11.5,
                borderBottom: "1px dotted var(--rule)",
              }}
            >
              {c.source_id}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── Reasoning block ─────────────────────────────────────────────── */

function ReasoningBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const lines = text.split("\n");
  const isLong = lines.length > 3 || text.length > 240;
  return (
    <div style={{ marginBottom: 12 }}>
      <div
        style={{
          padding: "8px 12px",
          background: "var(--surface-2)",
          borderLeft: "2px solid var(--accent)",
          borderRadius: 4,
          color: "var(--ink)",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.55,
          whiteSpace: "pre-wrap",
          maxHeight: open ? 280 : "calc(1.55em * 3)",
          overflowY: open ? "auto" : "hidden",
          position: "relative",
        }}
      >
        {text}
        {!open && isLong && (
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              bottom: 0,
              height: 22,
              background:
                "linear-gradient(to bottom, transparent, var(--surface-2))",
              pointerEvents: "none",
            }}
          />
        )}
      </div>
      {isLong && (
        <button
          type="button"
          onClick={() => setOpen((x) => !x)}
          className="btn-ghost btn"
          style={{
            marginTop: 4,
            padding: "2px 0",
            fontSize: 11,
            color: "var(--accent)",
            background: "transparent",
            border: "none",
          }}
        >
          {open ? "▾ Hide full reasoning" : "▸ Show full reasoning"}
        </button>
      )}
    </div>
  );
}

/* ── Agent-specific bodies ───────────────────────────────────────── */

function MetaPauserBody({ run }: { run: AgentRun }) {
  const trig = asRecord(run.trigger);
  const ev = asRecord(run.evidence);
  const evFeatures = asRecord(ev.features);
  const campaigns =
    asArray(trig.campaigns).length > 0
      ? asArray(trig.campaigns)
      : asArray(evFeatures.campaigns);

  const pa = asRecord(run.proposed_action);
  const payload = asRecord(pa.payload);
  const proposals = asArray(payload.proposals);
  const proposalIds = new Set(
    proposals
      .map((p) => asString(asRecord(p).campaign_id))
      .filter((x): x is string => Boolean(x)),
  );

  if (campaigns.length === 0 && proposals.length === 0) {
    return (
      <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>
        No campaign data attached to this run.
      </div>
    );
  }

  const learningCount = campaigns.filter(
    (c) => asRecord(c).learning_phase === true,
  ).length;
  const allLearning =
    campaigns.length > 0 && learningCount === campaigns.length;

  return (
    <>
      {allLearning && (
        <div
          style={{
            display: "flex",
            gap: 10,
            alignItems: "flex-start",
            padding: "10px 12px",
            borderRadius: 5,
            background: "var(--warn-soft)",
            color: "var(--warn)",
            fontSize: 12.5,
            lineHeight: 1.5,
            marginBottom: 10,
          }}
        >
          <span className="dot" style={{ marginTop: 6 }} />
          <span>
            <strong>All {campaigns.length} campaigns are in Meta&apos;s learning phase.</strong>{" "}
            Meta hasn&apos;t attributed revenue yet (conversions are below its
            learning threshold) — so attribution columns read ₹0. Agent
            defers all decisions until exit-learning.
          </span>
        </div>
      )}
      <div className="h-section" style={{ marginBottom: 6 }}>
        Campaigns evaluated · {campaigns.length}
        {!allLearning && learningCount > 0 && (
          <span style={{ color: "var(--ink-dim)", marginLeft: 6 }}>
            ({learningCount} in learning)
          </span>
        )}
      </div>
      <div
        style={{
          border: "1px solid var(--rule)",
          borderRadius: 6,
          overflow: "hidden",
        }}
      >
        <div style={{ overflowX: "auto" }}>
          <table className="dtable">
            <thead>
              <tr>
                <th>Campaign</th>
                <th className="right">Spend</th>
                <th className="right">Conv.</th>
                <th className="right">Attr. rev</th>
                <th className="right">RTO-adj rev</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((cRaw, i) => {
                const c = asRecord(cRaw);
                const name = asString(c.name) ?? asString(c.campaign_id) ?? "—";
                const cid = asString(c.campaign_id) ?? "";
                const spend = asNumber(c.spend);
                const conv = asNumber(c.conversions);
                const attr = asNumber(c.attributed_revenue);
                const rtoAdj = asNumber(c.rto_adjusted_revenue);
                const learning = c.learning_phase === true;
                const willPause = proposalIds.has(cid);
                return (
                  <tr key={cid || i}>
                    <td>
                      <div style={{ color: "var(--ink)", fontWeight: 500 }}>
                        {name}
                      </div>
                      <div
                        className="font-mono"
                        style={{ fontSize: 10.5, color: "var(--ink-dim)" }}
                      >
                        {cid ? `${cid.slice(0, 8)}…${cid.slice(-4)}` : ""}
                      </div>
                    </td>
                    <td className="right num">{inr(spend)}</td>
                    <td className="right num">{conv ?? "—"}</td>
                    <td
                      className="right num"
                      style={{ color: learning ? "var(--ink-dim)" : "var(--ink)" }}
                    >
                      {inr(attr)}
                    </td>
                    <td
                      className="right num"
                      style={{
                        color: learning
                          ? "var(--ink-dim)"
                          : rtoAdj != null && rtoAdj < (attr ?? 0)
                            ? "var(--warn)"
                            : "var(--ink)",
                      }}
                    >
                      {inr(rtoAdj)}
                    </td>
                    <td>
                      {willPause ? (
                        <span className="badge badge-danger">Pause</span>
                      ) : learning ? (
                        <span
                          className="badge badge-neutral"
                          title="Meta is still in learning phase for this campaign"
                        >
                          Learning
                        </span>
                      ) : (
                        <span className="badge badge-ok">Keep</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function PincodeBlockerBody({ run }: { run: AgentRun }) {
  const pa = asRecord(run.proposed_action);
  const payload = asRecord(pa.payload);
  const proposals = asArray(payload.proposals);

  const trig = asRecord(run.trigger);
  const ev = asRecord(run.evidence);
  const evFeatures = asRecord(ev.features);
  const allPincodes =
    asArray(trig.pincode_stats).length > 0
      ? asArray(trig.pincode_stats)
      : asArray(evFeatures.pincode_stats);

  const blockedSet = new Set(
    proposals
      .map((p) => asString(asRecord(p).pincode))
      .filter((x): x is string => Boolean(x)),
  );

  type Row = {
    pincode: string;
    rto_rate: number | null;
    sample_size: number | null;
    avg_cart_value: number | null;
    expected_loss: number | null;
    blocked: boolean;
  };
  const proposalByPin = new Map<string, Record<string, unknown>>();
  for (const p of proposals) {
    const r = asRecord(p);
    const pin = asString(r.pincode);
    if (pin) proposalByPin.set(pin, r);
  }

  const rows: Row[] = (allPincodes.length > 0
    ? allPincodes
    : proposals
  ).map((p) => {
    const r = asRecord(p);
    const pin = asString(r.pincode) ?? "";
    const pr = proposalByPin.get(pin) ?? r;
    return {
      pincode: pin,
      rto_rate: asNumber(r.rto_rate ?? pr.rto_rate),
      sample_size: asNumber(r.sample_size ?? pr.sample_size),
      avg_cart_value: asNumber(r.avg_cart_value ?? pr.avg_cart_value),
      expected_loss: asNumber(pr.expected_loss_per_order_inr),
      blocked: blockedSet.has(pin),
    };
  });

  rows.sort((a, b) => {
    if (a.blocked !== b.blocked) return a.blocked ? -1 : 1;
    return (b.rto_rate ?? 0) - (a.rto_rate ?? 0);
  });

  if (rows.length === 0) {
    return (
      <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>
        No pincode data attached to this run.
      </div>
    );
  }

  return (
    <>
      <div className="h-section" style={{ marginBottom: 6 }}>
        Pincodes evaluated · {rows.length} · {blockedSet.size} flagged
      </div>
      <div
        style={{
          border: "1px solid var(--rule)",
          borderRadius: 6,
          overflow: "hidden",
        }}
      >
        <div style={{ overflowX: "auto" }}>
          <table className="dtable">
            <thead>
              <tr>
                <th>Pincode</th>
                <th className="right">RTO rate</th>
                <th className="right">Orders</th>
                <th className="right">Avg cart</th>
                <th className="right">Loss / order</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.pincode || i}>
                  <td className="font-mono">{r.pincode || "—"}</td>
                  <td
                    className="right num"
                    style={{
                      color:
                        (r.rto_rate ?? 0) > 0.2
                          ? "var(--danger)"
                          : (r.rto_rate ?? 0) > 0.1
                            ? "var(--warn)"
                            : "var(--ink)",
                      fontWeight: r.blocked ? 600 : 400,
                    }}
                  >
                    {pct(r.rto_rate)}
                  </td>
                  <td className="right num">{r.sample_size ?? "—"}</td>
                  <td className="right num">{inr(r.avg_cart_value)}</td>
                  <td className="right num">{inr(r.expected_loss)}</td>
                  <td>
                    {r.blocked ? (
                      <span className="badge badge-danger">Block COD</span>
                    ) : (
                      <span className="badge badge-neutral">Allow</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function RtoRiskBody({ run }: { run: AgentRun }) {
  const trig = asRecord(run.trigger);
  const ev = asRecord(run.evidence);
  const evFeatures = asRecord(ev.features);

  const scalarFeats: [string, number][] = [];
  for (const [k, v] of Object.entries(evFeatures)) {
    const n = asNumber(v);
    if (n != null) scalarFeats.push([k, n]);
  }
  scalarFeats.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));

  const action = asRecord(run.proposed_action);
  const offer = asNumber(action.offer_pct);
  const channel = asString(action.channel);
  const actionType = asString(action.type ?? action.action_type);

  const orderId = asString(trig.order_id);
  const gateway = asString(trig.gateway);
  const amount = asNumber(trig.amount_inr);
  const pincode = asString(trig.pincode);

  return (
    <>
      {(orderId || amount != null) && (
        <div
          className="card"
          style={{
            padding: "10px 12px",
            background: "var(--surface-2)",
            display: "flex",
            flexWrap: "wrap",
            gap: 16,
            fontSize: 12.5,
          }}
        >
          {orderId && (
            <div>
              <div className="eyebrow">Order</div>
              <div className="font-mono" style={{ marginTop: 2 }}>
                {orderId}
              </div>
            </div>
          )}
          {amount != null && (
            <div>
              <div className="eyebrow">Amount</div>
              <div className="num" style={{ marginTop: 2, fontWeight: 500 }}>
                {inr(amount)}
              </div>
            </div>
          )}
          {gateway && (
            <div>
              <div className="eyebrow">Gateway</div>
              <div style={{ marginTop: 2 }}>{gateway}</div>
            </div>
          )}
          {pincode && (
            <div>
              <div className="eyebrow">Pincode</div>
              <div className="font-mono" style={{ marginTop: 2 }}>
                {pincode}
              </div>
            </div>
          )}
        </div>
      )}

      {actionType && (
        <div style={{ marginTop: 14 }}>
          <div className="h-section" style={{ marginBottom: 6 }}>
            Proposed action
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span className="badge badge-accent">{actionType}</span>
            {offer != null && (
              <span style={{ fontSize: 13 }}>
                offer{" "}
                <span className="num" style={{ fontWeight: 600 }}>
                  {offer}%
                </span>{" "}
                prepaid switch
              </span>
            )}
            {channel && (
              <span style={{ fontSize: 13, color: "var(--ink-soft)" }}>
                via {channel}
              </span>
            )}
          </div>
        </div>
      )}

      {scalarFeats.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="h-section" style={{ marginBottom: 6 }}>
            Features · {scalarFeats.length}
          </div>
          <div
            style={{
              border: "1px solid var(--rule)",
              borderRadius: 6,
              overflow: "hidden",
            }}
          >
            <table className="dtable">
              <tbody>
                {scalarFeats.map(([k, v]) => (
                  <tr key={k}>
                    <td
                      className="font-mono"
                      style={{ fontSize: 12, color: "var(--ink-soft)" }}
                    >
                      {k.replace(/_/g, " ")}
                    </td>
                    <td className="right num" style={{ width: 110 }}>
                      {num(v)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

/* ── Run card (with dedupe-group support) ────────────────────────── */

type RunGroup = {
  latest: AgentRun;
  count: number;
  oldest_at: string;
};

function RunCard({ group }: { group: RunGroup }) {
  const run = group.latest;
  const body =
    run.agent_id === "meta_pauser" ? (
      <MetaPauserBody run={run} />
    ) : run.agent_id === "pincode_cod_blocker" ? (
      <PincodeBlockerBody run={run} />
    ) : (
      <RtoRiskBody run={run} />
    );

  return (
    <article className="card" style={{ padding: 18, marginBottom: 14 }}>
      <header
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 16,
          marginBottom: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className={bandPillClass(run.band)}>
            <span className="dot" /> {run.band ?? "—"}
          </span>
          <span
            className="badge badge-neutral"
            style={{ textTransform: "none", letterSpacing: 0 }}
          >
            {agentLabel(run.agent_id)}
          </span>
          {group.count > 1 && (
            <span
              className="badge badge-accent"
              title={`Same decision repeated ${group.count} times in window`}
            >
              × {group.count} identical
            </span>
          )}
          <span
            className="font-mono"
            style={{
              fontSize: 11,
              color: "var(--ink-dim)",
              letterSpacing: "0.04em",
            }}
          >
            #{shortRunId(run.run_id)}
          </span>
          <span
            className="font-mono"
            style={{
              fontSize: 11,
              color: "var(--ink-dim)",
              letterSpacing: "0.02em",
            }}
          >
            {fmtTime(run.triggered_at)}
            {group.count > 1 && ` · earliest ${fmtTime(group.oldest_at)}`}
          </span>
        </div>

        <div style={{ textAlign: "right" }}>
          <div
            className="num"
            style={{
              fontSize: 22,
              fontWeight: 600,
              letterSpacing: "-0.01em",
              color:
                (run.expected_savings_inr ?? 0) > 0
                  ? "var(--ink)"
                  : "var(--ink-soft)",
            }}
          >
            {inr(run.expected_savings_inr)}
          </div>
          <div className="eyebrow" style={{ marginTop: 2 }}>
            Expected savings
            {run.score != null && (
              <>
                <span style={{ margin: "0 6px", color: "var(--ink-dim)" }}>·</span>
                <span>score {num(run.score, 2)}</span>
              </>
            )}
          </div>
        </div>
      </header>

      {run.reasoning && <ReasoningBlock text={run.reasoning} />}

      {body}

      <CitedList cits={run.cited_provenance ?? []} />
    </article>
  );
}

/* ── Empty state per agent ───────────────────────────────────────── */

function EmptyAgentState({ agentId }: { agentId: string | null }) {
  if (agentId === null) {
    return (
      <div
        className="card"
        style={{ padding: 24, textAlign: "center", color: "var(--ink-soft)" }}
      >
        <div style={{ fontSize: 14, marginBottom: 4, color: "var(--ink)" }}>
          The bench is quiet.
        </div>
        <div style={{ fontSize: 13 }}>
          No agent has proposed anything in this window — usually a good sign,
          occasionally a sign your webhooks aren&apos;t firing.
        </div>
      </div>
    );
  }
  const meta = AGENT_BY_ID[agentId];
  const isWebhookAgent = agentId === "rto_risk_flagger";
  return (
    <div
      className="card"
      style={{
        padding: 24,
        textAlign: "center",
        color: "var(--ink-soft)",
        maxWidth: 560,
        margin: "0 auto",
      }}
    >
      <div className="eyebrow" style={{ marginBottom: 6 }}>
        {meta?.label ?? agentId}
      </div>
      <div style={{ fontSize: 14, marginBottom: 4, color: "var(--ink)" }}>
        No runs from this agent yet.
      </div>
      <div style={{ fontSize: 13, lineHeight: 1.55 }}>
        {meta?.hint && <>{meta.hint}. </>}
        {isWebhookAgent
          ? "It fires when a new order is created — none have arrived in the demo window."
          : "Its scheduled cron has produced no runs in the current window."}
      </div>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

type TriggerToast = {
  tone: "ok" | "err";
  text: string;
  hint?: string;
} | null;

export function AgentBench() {
  const { tenantId } = useTenant();
  const [allRuns, setAllRuns] = useState<AgentRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [toast, setToast] = useState<TriggerToast>(null);

  const load = useCallback(async () => {
    if (!tenantId) return;
    setLoading(true);
    setError(null);
    try {
      // Fetch all runs once; filter client-side so chip counts are accurate.
      const rows = await fetchRuns(tenantId);
      setAllRuns(rows);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "fetch failed");
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Auto-dismiss the toast after a few seconds so repeated clicks aren't loud.
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(id);
  }, [toast]);

  const fireWebhook = useCallback(async () => {
    if (!tenantId || triggering) return;
    setTriggering("webhook");
    setToast(null);
    try {
      const out = await triggerWebhook(tenantId);
      const s = out.summary;
      setToast({
        tone: "ok",
        text: `Webhook fired — pincode ${s.pincode}, cart ₹${s.cart_value_inr.toLocaleString("en-IN")}`,
        hint: `RTO Flagger should produce a fresh run; click Refresh in ~3s.`,
      });
      // Give the worker a beat, then refresh.
      setTimeout(() => void load(), 2500);
    } catch (e) {
      setToast({ tone: "err", text: e instanceof Error ? e.message : "trigger failed" });
    } finally {
      setTriggering(null);
    }
  }, [tenantId, triggering, load]);

  const fireCron = useCallback(
    async (agentId: string) => {
      if (!tenantId || triggering) return;
      setTriggering(`cron:${agentId}`);
      setToast(null);
      try {
        const out = await triggerCron(tenantId, agentId);
        const savings = out.expected_savings_inr ?? 0;
        setToast({
          tone: "ok",
          text: `${agentId} ran — band ${out.band ?? "—"}, savings ₹${savings.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`,
        });
        await load();
      } catch (e) {
        setToast({ tone: "err", text: e instanceof Error ? e.message : "trigger failed" });
      } finally {
        setTriggering(null);
      }
    },
    [tenantId, triggering, load],
  );

  // Per-agent counts for filter chip badges and disabled state.
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of allRuns) c[r.agent_id] = (c[r.agent_id] ?? 0) + 1;
    return c;
  }, [allRuns]);

  const filtered = useMemo(
    () =>
      filter == null ? allRuns : allRuns.filter((r) => r.agent_id === filter),
    [allRuns, filter],
  );

  // Group consecutive identical runs.
  const groups = useMemo<RunGroup[]>(() => {
    // Sort newest first by triggered_at.
    const sorted = [...filtered].sort(
      (a, b) =>
        new Date(b.triggered_at).getTime() - new Date(a.triggered_at).getTime(),
    );
    const out: RunGroup[] = [];
    for (const r of sorted) {
      const fp = runFingerprint(r);
      const last = out[out.length - 1];
      if (last && runFingerprint(last.latest) === fp) {
        last.count += 1;
        last.oldest_at = r.triggered_at;
      } else {
        out.push({ latest: r, count: 1, oldest_at: r.triggered_at });
      }
    }
    return out;
  }, [filtered]);

  const stats = useMemo(() => {
    const total = filtered.length;
    const high = filtered.filter((r) => r.band === "HIGH").length;
    const totalSavings = filtered.reduce(
      (sum, r) => sum + (r.expected_savings_inr ?? 0),
      0,
    );
    return { total, high, totalSavings };
  }, [filtered]);

  return (
    <main
      style={{
        maxWidth: 1400,
        margin: "0 auto",
        padding: "clamp(16px, 3vw, 28px) clamp(1rem, 3vw, 2rem) 5rem",
      }}
    >
      <header style={{ marginBottom: 18 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 6,
          }}
        >
          <span className="h-section">Agent runs</span>
          <span
            className="font-mono"
            style={{ fontSize: 11, color: "var(--ink-dim)" }}
          >
            dry-run · never executed
          </span>
        </div>
        <h1 className="h-display" style={{ margin: 0, marginBottom: 10 }}>
          What the agents would do.
        </h1>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 10,
            marginTop: 14,
          }}
        >
          <div className="card" style={{ padding: 12 }}>
            <div className="eyebrow">
              {filter ? `${agentLabel(filter)} runs` : "Runs in window"}
            </div>
            <div
              className="num"
              style={{
                fontSize: 22,
                fontWeight: 600,
                marginTop: 2,
                letterSpacing: "-0.01em",
              }}
            >
              {stats.total}
            </div>
          </div>
          <div className="card" style={{ padding: 12 }}>
            <div className="eyebrow">High-severity</div>
            <div
              className="num"
              style={{
                fontSize: 22,
                fontWeight: 600,
                marginTop: 2,
                color: stats.high > 0 ? "var(--danger)" : "var(--ink)",
                letterSpacing: "-0.01em",
              }}
            >
              {stats.high}
            </div>
          </div>
          <div className="card" style={{ padding: 12 }}>
            <div className="eyebrow">Aggregate expected savings</div>
            <div
              className="num"
              style={{
                fontSize: 22,
                fontWeight: 600,
                marginTop: 2,
                letterSpacing: "-0.01em",
              }}
            >
              {inr(stats.totalSavings)}
            </div>
          </div>
        </div>
      </header>

      {/* Filters */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 10,
          padding: "10px 0",
          borderTop: "1px solid var(--rule)",
          borderBottom: "1px solid var(--rule)",
          marginBottom: 14,
        }}
      >
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <FilterChip
            label="All"
            active={filter === null}
            onClick={() => setFilter(null)}
            count={allRuns.length}
            disabled={false}
          />
          {AGENTS.map((a) => (
            <FilterChip
              key={a.id}
              label={a.label}
              active={filter === a.id}
              onClick={() => setFilter(a.id)}
              count={counts[a.id] ?? 0}
              disabled={(counts[a.id] ?? 0) === 0}
            />
          ))}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={() => void fireWebhook()}
            className="btn"
            disabled={triggering !== null}
            title="POST a randomized Shopify order webhook — exercises ingest → RTO Flagger end-to-end"
          >
            {triggering === "webhook" ? "Firing…" : "Fire webhook"}
          </button>
          <button
            type="button"
            onClick={() => void fireCron("pincode_cod_blocker")}
            className="btn"
            disabled={triggering !== null}
            title="Run the Pincode COD Blocker cron once against live data"
          >
            {triggering === "cron:pincode_cod_blocker" ? "Running…" : "Run Pincode cron"}
          </button>
          <button
            type="button"
            onClick={() => void fireCron("meta_pauser")}
            className="btn"
            disabled={triggering !== null}
            title="Run the Meta Pauser cron once against live data"
          >
            {triggering === "cron:meta_pauser" ? "Running…" : "Run Meta cron"}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            className="btn"
            disabled={loading}
          >
            <span aria-hidden style={{ display: "inline-block", width: 12 }}>
              ↻
            </span>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {toast && (
        <div
          className="card"
          role="status"
          style={{
            padding: 12,
            marginBottom: 14,
            background:
              toast.tone === "ok" ? "var(--ok-soft)" : "var(--danger-soft)",
            borderColor:
              toast.tone === "ok"
                ? "color-mix(in oklch, var(--ok) 40%, var(--rule))"
                : "color-mix(in oklch, var(--danger) 40%, var(--rule))",
            color: toast.tone === "ok" ? "var(--ok)" : "var(--danger)",
            fontSize: 13,
          }}
        >
          <div style={{ fontWeight: 500 }}>{toast.text}</div>
          {toast.hint && (
            <div
              style={{ marginTop: 4, color: "var(--ink-soft)", fontSize: 12 }}
            >
              {toast.hint}
            </div>
          )}
        </div>
      )}

      {error && (
        <div
          className="card"
          style={{
            padding: 14,
            background: "var(--danger-soft)",
            borderColor: "color-mix(in oklch, var(--danger) 40%, var(--rule))",
            color: "var(--danger)",
            fontSize: 13,
            marginBottom: 14,
          }}
        >
          Could not load runs: {error}.
        </div>
      )}

      {!error && groups.length === 0 && !loading && (
        <EmptyAgentState agentId={filter} />
      )}

      <div>
        {groups.map((g) => (
          <RunCard key={g.latest.run_id} group={g} />
        ))}
      </div>
    </main>
  );
}

function FilterChip({
  label,
  active,
  onClick,
  count,
  disabled,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  count: number;
  disabled: boolean;
}) {
  const isAll = label === "All";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled && !active}
      style={{
        background: active ? "var(--accent-soft)" : "transparent",
        color: active
          ? "var(--accent)"
          : disabled
            ? "var(--ink-dim)"
            : "var(--ink-soft)",
        border: `1px solid ${active ? "color-mix(in oklch, var(--accent) 35%, var(--rule))" : "var(--rule)"}`,
        borderRadius: 999,
        padding: "4px 10px",
        fontSize: 12,
        fontWeight: 500,
        cursor: disabled && !active ? "default" : "pointer",
        opacity: disabled && !active ? 0.5 : 1,
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
      }}
      title={
        disabled && !active
          ? `${label} produced no runs in the current window`
          : undefined
      }
    >
      {label}
      {!isAll || count > 0 ? (
        <span
          className="font-mono"
          style={{
            fontSize: 10,
            color: active ? "var(--accent)" : "var(--ink-dim)",
          }}
        >
          {count}
        </span>
      ) : null}
    </button>
  );
}
