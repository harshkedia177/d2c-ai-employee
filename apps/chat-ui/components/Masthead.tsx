"use client";

import { useEffect, useRef, useState } from "react";
import type { Tenant } from "@/lib/api";

type Props = {
  tenants: Tenant[];
  selectedTenantId: string;
  onChange: (id: string) => void;
  backendOk: boolean;
};

function shortId(id: string): string {
  if (!id) return "";
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

export function Masthead({
  tenants,
  selectedTenantId,
  onChange,
  backendOk,
}: Props) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);

  const selected = tenants.find((t) => t.tenant_id === selectedTenantId);

  const copy = async () => {
    if (!selectedTenantId) return;
    try {
      await navigator.clipboard.writeText(selectedTenantId);
      setCopied(true);
      setTimeout(() => setCopied(false), 1100);
    } catch {
      /* noop */
    }
  };

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 50,
        background: "color-mix(in oklch, var(--bg) 85%, transparent)",
        backdropFilter: "saturate(140%) blur(8px)",
        WebkitBackdropFilter: "saturate(140%) blur(8px)",
        borderBottom: "1px solid var(--rule)",
      }}
    >
      <div
        style={{
          maxWidth: 1400,
          margin: "0 auto",
          padding: "12px clamp(1rem, 3vw, 2rem)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
        }}
      >
        {/* Brand */}
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div
            aria-hidden
            style={{
              width: 22,
              height: 22,
              borderRadius: 5,
              background: "var(--accent)",
              display: "grid",
              placeItems: "center",
              color: "var(--accent-ink)",
              fontFamily: "var(--font-mono)",
              fontWeight: 700,
              fontSize: 12,
              letterSpacing: "-0.02em",
            }}
          >
            S
          </div>
          <div style={{ display: "flex", flexDirection: "column", lineHeight: 1 }}>
            <div
              style={{
                fontSize: 14,
                fontWeight: 600,
                letterSpacing: "-0.01em",
                color: "var(--ink)",
              }}
            >
              Shoppin
              <span
                className="font-mono"
                style={{
                  marginLeft: 6,
                  fontSize: 11,
                  color: "var(--ink-dim)",
                  fontWeight: 400,
                  letterSpacing: "0.04em",
                }}
              >
                / D2C AI Employee
              </span>
            </div>
            <div
              className="font-mono"
              style={{
                marginTop: 4,
                fontSize: 10,
                letterSpacing: "0.08em",
                color: "var(--ink-dim)",
                textTransform: "uppercase",
              }}
            >
              v0 · issue 01
            </div>
          </div>
        </div>

        {/* Right cluster */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            className="pill"
            title={backendOk ? "Backend reachable" : "Backend unreachable"}
            style={{
              color: backendOk ? "var(--ok)" : "var(--danger)",
              borderColor: backendOk
                ? "color-mix(in oklch, var(--ok) 40%, var(--rule))"
                : "color-mix(in oklch, var(--danger) 40%, var(--rule))",
              background: "transparent",
            }}
          >
            <span className="dot" />
            {backendOk ? "online" : "offline"}
          </span>

          <div ref={ref} style={{ position: "relative" }}>
            <button
              type="button"
              onClick={() => setOpen((x) => !x)}
              className="btn"
              style={{ paddingRight: 8 }}
            >
              <span
                className="eyebrow"
                style={{ color: "var(--ink-dim)", margin: 0 }}
              >
                Tenant
              </span>
              <span style={{ color: "var(--ink)" }}>
                {selected?.slug ?? "—"}
              </span>
              <span
                aria-hidden
                style={{
                  color: "var(--ink-dim)",
                  fontSize: 10,
                  marginLeft: 2,
                }}
              >
                ▾
              </span>
            </button>

            {open && (
              <div
                role="menu"
                style={{
                  position: "absolute",
                  right: 0,
                  top: "calc(100% + 6px)",
                  minWidth: 280,
                  background: "var(--surface)",
                  border: "1px solid var(--rule)",
                  borderRadius: 8,
                  boxShadow:
                    "0 8px 24px color-mix(in oklch, var(--ink) 12%, transparent)",
                  padding: 6,
                  zIndex: 60,
                }}
              >
                <div
                  className="eyebrow"
                  style={{
                    padding: "6px 8px 4px",
                    borderBottom: "1px solid var(--rule-soft)",
                    marginBottom: 4,
                  }}
                >
                  Switch tenant
                </div>
                {tenants.length === 0 && (
                  <div
                    style={{
                      padding: "8px 10px",
                      color: "var(--ink-dim)",
                      fontSize: 13,
                    }}
                  >
                    No tenants returned.
                  </div>
                )}
                {tenants.map((t) => {
                  const active = t.tenant_id === selectedTenantId;
                  return (
                    <button
                      key={t.tenant_id}
                      role="menuitem"
                      onClick={() => {
                        onChange(t.tenant_id);
                        setOpen(false);
                      }}
                      style={{
                        width: "100%",
                        textAlign: "left",
                        background: active ? "var(--surface-2)" : "transparent",
                        border: "none",
                        cursor: "pointer",
                        padding: "8px 10px",
                        borderRadius: 5,
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                      }}
                    >
                      <span
                        className="dot"
                        style={{
                          background: active ? "var(--accent)" : "transparent",
                          border: active ? "none" : "1px solid var(--rule)",
                        }}
                      />
                      <span style={{ flex: 1 }}>
                        <div style={{ fontSize: 13, color: "var(--ink)" }}>
                          {t.slug}
                        </div>
                        <div
                          className="font-mono"
                          style={{ fontSize: 10.5, color: "var(--ink-dim)" }}
                        >
                          {shortId(t.tenant_id)}
                        </div>
                      </span>
                    </button>
                  );
                })}
                {selectedTenantId && (
                  <button
                    type="button"
                    onClick={copy}
                    className="btn-ghost btn"
                    style={{
                      width: "100%",
                      justifyContent: "space-between",
                      marginTop: 4,
                      borderTop: "1px solid var(--rule-soft)",
                      borderRadius: 0,
                      paddingTop: 8,
                      paddingBottom: 8,
                    }}
                  >
                    <span
                      className="eyebrow"
                      style={{ color: "var(--ink-soft)" }}
                    >
                      {copied ? "Copied" : "Copy tenant_id"}
                    </span>
                    <span
                      className="font-mono"
                      style={{ fontSize: 10.5, color: "var(--ink-dim)" }}
                    >
                      {shortId(selectedTenantId)}
                    </span>
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
