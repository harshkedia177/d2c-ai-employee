"use client";

import { useEffect, useState } from "react";
import { fetchMetrics, type DimensionDef, type MetricDef } from "@/lib/api";

export function MetricsCatalogue() {
  const [data, setData] = useState<
    { metrics: MetricDef[]; dimensions: DimensionDef[] } | null
  >(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchMetrics()
      .then(setData)
      .catch((e) => setErr(String(e)));
  }, []);

  const pagePad: React.CSSProperties = {
    paddingTop: "clamp(1.5rem, 4vw, 3rem)",
    paddingBottom: "clamp(2rem, 5vw, 5rem)",
    marginLeft: "clamp(2rem, 8vw, 10rem)",
    marginRight: "clamp(2rem, 5vw, 5rem)",
    maxWidth: 1400,
  };

  if (err) {
    return (
      <main style={pagePad}>
        <p
          style={{
            color: "var(--danger)",
            fontFamily: "var(--font-geist-sans), system-ui, sans-serif",
          }}
        >
          Failed to load: {err}
        </p>
      </main>
    );
  }

  if (!data) {
    return (
      <main style={pagePad}>
        <p
          style={{
            fontFamily: "var(--font-fraunces), Georgia, serif",
            fontStyle: "italic",
            color: "var(--ink-soft)",
          }}
        >
          Loading the catalogue…
        </p>
      </main>
    );
  }

  return (
    <main style={pagePad}>
      <header style={{ marginBottom: "clamp(2rem, 4vw, 3.5rem)" }}>
        <div className="eyebrow" style={{ marginBottom: "0.75rem" }}>
          SEMANTIC LAYER · {data.metrics.length} METRICS ·{" "}
          {data.dimensions.length} DIMENSIONS
        </div>
        <h1
          className="headline"
          style={{
            fontSize: "clamp(2rem, 4vw, 3.5rem)",
            margin: 0,
            color: "var(--ink)",
          }}
        >
          Metrics &amp; Dimensions
        </h1>
        <p
          className="editorial-body"
          style={{
            marginTop: "1rem",
            maxWidth: "44ch",
            color: "var(--ink-soft)",
          }}
        >
          The contract between every numerical claim the chat surface returns
          and the rows in the warehouse that back it. Edit{" "}
          <span
            style={{
              fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
              fontSize: "0.92em",
            }}
          >
            packages/semantic_layer/metrics.yml
          </span>{" "}
          to extend.
        </p>
      </header>

      <section style={{ marginTop: "clamp(2rem, 4vw, 3rem)" }}>
        <h2
          className="headline-italic"
          style={{
            fontSize: "1.5rem",
            margin: 0,
            paddingBottom: "0.5rem",
            borderBottom: "1px solid var(--rule)",
            color: "var(--ink)",
          }}
        >
          Metrics
        </h2>
        <dl
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 14rem) minmax(0, 1fr)",
            columnGap: "2.5rem",
            rowGap: "1.75rem",
            marginTop: "1.75rem",
          }}
        >
          {data.metrics.map((m) => (
            <div key={m.id} style={{ display: "contents" }}>
              <dt
                className="font-mono"
                style={{
                  fontSize: "0.85rem",
                  color: "var(--accent)",
                  paddingTop: "0.15rem",
                  letterSpacing: "0.02em",
                }}
              >
                {m.id}
              </dt>
              <dd
                style={{
                  margin: 0,
                  fontFamily: "var(--font-fraunces), Georgia, serif",
                  fontSize: "1.05rem",
                  lineHeight: 1.55,
                  color: "var(--ink)",
                }}
              >
                {m.description}
                <span
                  className="eyebrow"
                  style={{
                    display: "block",
                    marginTop: "0.4rem",
                    color: "var(--ink-soft)",
                  }}
                >
                  grain · {m.grain}
                  {m.min_sample_size
                    ? ` · min sample ${m.min_sample_size}`
                    : ""}
                </span>
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section style={{ marginTop: "clamp(2.5rem, 5vw, 4rem)" }}>
        <h2
          className="headline-italic"
          style={{
            fontSize: "1.5rem",
            margin: 0,
            paddingBottom: "0.5rem",
            borderBottom: "1px solid var(--rule)",
            color: "var(--ink)",
          }}
        >
          Dimensions
        </h2>
        <dl
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 14rem) minmax(0, 1fr)",
            columnGap: "2.5rem",
            rowGap: "1.25rem",
            marginTop: "1.75rem",
          }}
        >
          {data.dimensions.map((d) => (
            <div key={d.id} style={{ display: "contents" }}>
              <dt
                className="font-mono"
                style={{
                  fontSize: "0.85rem",
                  color: "var(--accent)",
                  paddingTop: "0.15rem",
                  letterSpacing: "0.02em",
                }}
              >
                {d.id}
              </dt>
              <dd
                className="font-mono"
                style={{
                  margin: 0,
                  fontSize: "0.82rem",
                  color: "var(--ink)",
                  wordBreak: "break-word",
                }}
              >
                {d.sql}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    </main>
  );
}
