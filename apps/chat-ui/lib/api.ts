// Thin client for the FastAPI backend. Server-or-client agnostic — no React.

const BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type Tenant = {
  tenant_id: string;
  slug: string;
  created_at: string;
};

export type Citation = {
  source_system: string;
  source_id: string;
  url: string;
  raw_table?: string | null;
  raw_row_id?: string | null;
};

export type Footnote = {
  placeholder?: string;
  query_hash?: string;
  metric_id?: string;
  citations: Citation[];
  total_sources?: number;
  sample_size?: number;
};

export type ChatResponse = {
  text: string;
  footnotes: Footnote[];
  status: string;
};

export type AgentRun = {
  run_id: string;
  agent_id: string;
  triggered_at: string;
  score: number | null;
  band: string | null;
  expected_savings_inr: number | null;
  reasoning: string | null;
  proposed_action: Record<string, unknown> | null;
  evidence: Record<string, unknown> | null;
  trigger: Record<string, unknown> | null;
  cited_provenance: Citation[] | null;
};

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return (await r.json()) as T;
}

export async function fetchTenants(): Promise<Tenant[]> {
  const data = await getJSON<{ tenants: Tenant[] }>("/tenants");
  return data.tenants;
}

export async function postChat(
  tenantId: string,
  message: string,
): Promise<ChatResponse> {
  const r = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tenant_id: tenantId, message }),
  });
  if (!r.ok) throw new Error(`/chat → ${r.status}`);
  return (await r.json()) as ChatResponse;
}

export async function fetchRuns(
  tenantId: string,
  agentId?: string,
): Promise<AgentRun[]> {
  const qs = new URLSearchParams({ tenant_id: tenantId, limit: "20" });
  if (agentId) qs.set("agent_id", agentId);
  const data = await getJSON<{ runs: AgentRun[] }>(`/runs?${qs.toString()}`);
  return data.runs;
}

export type MetricDef = {
  id: string;
  description: string;
  grain: string;
  min_sample_size?: number;
};

export type DimensionDef = { id: string; sql: string };

export async function fetchMetrics(): Promise<{
  metrics: MetricDef[];
  dimensions: DimensionDef[];
}> {
  return getJSON<{ metrics: MetricDef[]; dimensions: DimensionDef[] }>(
    "/metrics",
  );
}

export const DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001";
