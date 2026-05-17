"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchMetrics, type DimensionDef, type MetricDef } from "@/lib/api";

type Data = { metrics: MetricDef[]; dimensions: DimensionDef[] };

export function MetricsCatalogue() {
  const [data, setData] = useState<Data | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    fetchMetrics()
      .then(setData)
      .catch((e) => setErr(String(e)));
  }, []);

  const { metrics, dimensions } = useMemo(() => {
    if (!data) return { metrics: [], dimensions: [] };
    const needle = q.trim().toLowerCase();
    if (!needle) return data;
    const m = data.metrics.filter(
      (x) =>
        x.id.toLowerCase().includes(needle) ||
        x.description.toLowerCase().includes(needle) ||
        x.grain.toLowerCase().includes(needle),
    );
    const d = data.dimensions.filter(
      (x) =>
        x.id.toLowerCase().includes(needle) ||
        x.sql.toLowerCase().includes(needle),
    );
    return { metrics: m, dimensions: d };
  }, [data, q]);

  return (
    <main
      style={{
        maxWidth: 1200,
        margin: "0 auto",
        padding: "clamp(16px, 3vw, 28px) clamp(1rem, 3vw, 2rem) 5rem",
      }}
    >
      <header style={{ marginBottom: 20 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 6,
          }}
        >
          <span className="h-section">Semantic layer</span>
          <span
            className="font-mono"
            style={{ fontSize: 11, color: "var(--ink-dim)" }}
          >
            metrics.yml
          </span>
        </div>
        <h1 className="h-display" style={{ margin: 0, marginBottom: 8 }}>
          The contract between numbers and rows.
        </h1>
        <p
          style={{
            color: "var(--ink-soft)",
            fontSize: 14,
            lineHeight: 1.55,
            maxWidth: "70ch",
            margin: 0,
          }}
        >
          Every numerical claim the chat returns resolves through one of these
          metric definitions. Each metric declares its{" "}
          <span className="code-inline">grain</span> and minimum sample size
          before it&apos;s allowed to answer — refusals are honest.
          Dimensions provide the SQL columns metrics can group by.
        </p>

        {!err && data && (
          <div
            style={{
              marginTop: 14,
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
              gap: 10,
            }}
          >
            <Stat label="Metrics" value={data.metrics.length} />
            <Stat label="Dimensions" value={data.dimensions.length} />
            <Stat
              label="Source file"
              value={
                <span className="font-mono" style={{ fontSize: 12 }}>
                  packages/semantic_layer/metrics.yml
                </span>
              }
            />
          </div>
        )}
      </header>

      {err && (
        <div
          className="card"
          style={{
            padding: 14,
            background: "var(--danger-soft)",
            color: "var(--danger)",
            fontSize: 13,
          }}
        >
          Failed to load: {err}
        </div>
      )}

      {!data && !err && (
        <div style={{ color: "var(--ink-soft)", fontSize: 14 }}>
          Loading the catalogue…
        </div>
      )}

      {data && (
        <>
          <div
            style={{
              position: "sticky",
              top: 64,
              zIndex: 30,
              background: "var(--bg)",
              padding: "8px 0 12px",
              borderBottom: "1px solid var(--rule)",
              marginBottom: 18,
              display: "flex",
              alignItems: "center",
              gap: 10,
              flexWrap: "wrap",
            }}
          >
            <input
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter by id, description, or SQL fragment…"
              className="input"
              style={{ flex: "1 1 280px", maxWidth: 460 }}
            />
            {q && (
              <span
                className="font-mono"
                style={{ fontSize: 11, color: "var(--ink-dim)" }}
              >
                {metrics.length} metric{metrics.length === 1 ? "" : "s"} ·{" "}
                {dimensions.length} dim{dimensions.length === 1 ? "" : "s"}
              </span>
            )}
          </div>

          <section style={{ marginBottom: 32 }}>
            <SectionHeader
              label="Metrics"
              count={metrics.length}
              total={data.metrics.length}
            />
            <div
              className="card"
              style={{ overflow: "hidden", padding: 0, marginTop: 10 }}
            >
              <table className="dtable">
                <thead>
                  <tr>
                    <th style={{ width: "20%" }}>ID</th>
                    <th>Description</th>
                    <th style={{ width: 120 }}>Grain</th>
                    <th className="right" style={{ width: 120 }}>
                      Min sample
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.length === 0 && (
                    <tr>
                      <td
                        colSpan={4}
                        style={{
                          padding: 18,
                          textAlign: "center",
                          color: "var(--ink-soft)",
                          fontSize: 13,
                        }}
                      >
                        No metrics match the filter.
                      </td>
                    </tr>
                  )}
                  {metrics.map((m) => (
                    <tr key={m.id}>
                      <td
                        className="font-mono"
                        style={{
                          fontSize: 12,
                          color: "var(--accent)",
                          fontWeight: 500,
                          letterSpacing: "0.01em",
                        }}
                      >
                        {m.id}
                      </td>
                      <td
                        style={{
                          fontSize: 13,
                          color: "var(--ink)",
                          lineHeight: 1.5,
                        }}
                      >
                        {m.description}
                      </td>
                      <td>
                        <span className="badge badge-neutral">{m.grain}</span>
                      </td>
                      <td className="right num">
                        {m.min_sample_size ? (
                          m.min_sample_size.toLocaleString()
                        ) : (
                          <span style={{ color: "var(--ink-dim)" }}>—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <SectionHeader
              label="Dimensions"
              count={dimensions.length}
              total={data.dimensions.length}
            />
            <div
              style={{
                display: "grid",
                gridTemplateColumns:
                  "repeat(auto-fill, minmax(min(420px, 100%), 1fr))",
                gap: 10,
                marginTop: 10,
              }}
            >
              {dimensions.length === 0 && (
                <div
                  className="card"
                  style={{
                    padding: 18,
                    textAlign: "center",
                    color: "var(--ink-soft)",
                    fontSize: 13,
                  }}
                >
                  No dimensions match the filter.
                </div>
              )}
              {dimensions.map((d) => (
                <div key={d.id} className="card" style={{ padding: 12 }}>
                  <div
                    className="font-mono"
                    style={{
                      fontSize: 12,
                      color: "var(--accent)",
                      fontWeight: 500,
                      marginBottom: 6,
                      letterSpacing: "0.01em",
                    }}
                  >
                    {d.id}
                  </div>
                  <pre
                    className="code-block"
                    style={{
                      margin: 0,
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }}
                  >
                    {d.sql}
                  </pre>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </main>
  );
}

function Stat({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="card" style={{ padding: 12 }}>
      <div className="eyebrow">{label}</div>
      <div
        style={{
          fontSize: 18,
          fontWeight: 600,
          marginTop: 4,
          color: "var(--ink)",
          letterSpacing: "-0.01em",
        }}
      >
        {value}
      </div>
    </div>
  );
}

function SectionHeader({
  label,
  count,
  total,
}: {
  label: string;
  count: number;
  total: number;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 8,
        paddingBottom: 4,
      }}
    >
      <h2
        style={{
          margin: 0,
          fontSize: 18,
          fontWeight: 600,
          color: "var(--ink)",
          letterSpacing: "-0.01em",
        }}
      >
        {label}
      </h2>
      <span
        className="font-mono"
        style={{ fontSize: 12, color: "var(--ink-dim)" }}
      >
        {count === total ? count : `${count}/${total}`}
      </span>
    </div>
  );
}
