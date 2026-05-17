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

// ---------------------------------------------------------------------------
// SSE streaming chat — POST /chat/stream returns text/event-stream.
// Frames are separated by "\n\n"; each frame may have `event: <name>` and one
// or more `data: <chunk>` lines (chunks are concatenated with "\n" before JSON
// parsing).
// ---------------------------------------------------------------------------

export type PlanTask = {
  task_id: string;
  tool: string;
  args: Record<string, unknown>;
};

export type SseEvent =
  | {
      event: "plan";
      data: { trace_id: string; tasks: PlanTask[] };
    }
  | {
      event: "tool_start";
      data: { task_id: string; tool: string; args: Record<string, unknown> };
    }
  | {
      event: "tool_result";
      data: { task_id: string; ok: boolean; summary: string };
    }
  | {
      event: "join_decision";
      data: { action: "finalize" | "replan"; hint?: string };
    }
  | { event: "compose_start"; data: { trace_id: string } }
  | { event: "token"; data: { text: string } }
  | { event: "footnote"; data: { footnote: Footnote } }
  | {
      event: "done";
      data: {
        status: string;
        usage?: Record<string, number>;
        trace_id: string;
      };
    }
  | {
      event: "error";
      data: { code: string; message: string; trace_id: string };
    };

type SseEventName = SseEvent["event"];

const SSE_EVENT_NAMES: ReadonlySet<SseEventName> = new Set<SseEventName>([
  "plan",
  "tool_start",
  "tool_result",
  "join_decision",
  "compose_start",
  "token",
  "footnote",
  "done",
  "error",
]);

function isKnownSseEventName(name: string): name is SseEventName {
  return (SSE_EVENT_NAMES as ReadonlySet<string>).has(name);
}

function parseSseFrame(frame: string): SseEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const rawLine of frame.split("\n")) {
    // Comment line (heartbeat etc.) — ignore.
    if (rawLine.startsWith(":")) continue;
    if (rawLine.startsWith("event:")) {
      eventName = rawLine.slice(6).trim();
    } else if (rawLine.startsWith("data:")) {
      // Per SSE spec: strip a single leading space if present.
      const v = rawLine.slice(5);
      dataLines.push(v.startsWith(" ") ? v.slice(1) : v);
    }
  }
  if (!dataLines.length) return null;
  if (!isKnownSseEventName(eventName)) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
  // The runtime check above narrows eventName to SseEventName; the data shape
  // is trusted to match the contract documented at the top of this section.
  return { event: eventName, data: payload } as SseEvent;
}

export async function* streamChat(
  tenantId: string,
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent, void, unknown> {
  const r = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ tenant_id: tenantId, message }),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`/chat/stream → ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE frames are separated by "\n\n". Drain every complete frame from
      // the buffer before reading more bytes.
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const ev = parseSseFrame(frame);
        if (ev) yield ev;
      }
    }
    // Flush any final frame that the server emitted without a trailing
    // blank line (defensive — most servers do terminate cleanly).
    buffer += decoder.decode();
    if (buffer.trim().length > 0) {
      const ev = parseSseFrame(buffer);
      if (ev) yield ev;
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* reader may already be released on abort */
    }
  }
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

export type WebhookTriggerSummary = {
  source_id: string;
  pincode: string;
  cart_value_inr: number;
  sku: string;
  customer_id: number;
};

export type WebhookTriggerResponse = {
  ok: boolean;
  raw_row_id: number;
  summary: WebhookTriggerSummary;
};

export type CronTriggerResponse = {
  ok: boolean;
  run_id: string;
  agent_id: string;
  band: string | null;
  expected_savings_inr: number | null;
  reasoning: string;
};

export async function triggerWebhook(
  tenantId: string,
): Promise<WebhookTriggerResponse> {
  const qs = new URLSearchParams({ tenant_id: tenantId });
  const r = await fetch(`${BASE}/triggers/webhook?${qs.toString()}`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(`/triggers/webhook → ${r.status}`);
  return (await r.json()) as WebhookTriggerResponse;
}

export async function triggerCron(
  tenantId: string,
  agentId: string,
): Promise<CronTriggerResponse> {
  const qs = new URLSearchParams({ tenant_id: tenantId });
  const r = await fetch(
    `${BASE}/triggers/cron/${encodeURIComponent(agentId)}?${qs.toString()}`,
    { method: "POST" },
  );
  if (!r.ok) throw new Error(`/triggers/cron → ${r.status}`);
  return (await r.json()) as CronTriggerResponse;
}
