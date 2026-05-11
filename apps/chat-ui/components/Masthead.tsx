"use client";

import { useEffect, useState } from "react";
import type { Tenant } from "@/lib/api";

type Props = {
  tenants: Tenant[];
  selectedTenantId: string;
  onChange: (id: string) => void;
};

function formatDate(d: Date): string {
  const day = d.getDate();
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const weekdays = [
    "Sunday", "Monday", "Tuesday", "Wednesday",
    "Thursday", "Friday", "Saturday",
  ];
  return `${day} ${months[d.getMonth()]} ${d.getFullYear()} · ${weekdays[d.getDay()]} Edition`;
}

export function Masthead({ tenants, selectedTenantId, onChange }: Props) {
  const [today, setToday] = useState<string>("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setToday(formatDate(new Date()));
  }, []);

  const tid = selectedTenantId;
  const truncated = tid ? `${tid.slice(0, 8)}…${tid.slice(-4)}` : "";

  const copy = async () => {
    if (!tid) return;
    try {
      await navigator.clipboard.writeText(tid);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* noop */
    }
  };

  return (
    <header
      style={{
        marginLeft: "clamp(2rem, 8vw, 10rem)",
        marginRight: "clamp(2rem, 5vw, 5rem)",
        paddingTop: "clamp(2rem, 4vw, 3rem)",
      }}
    >
      <div className="grid grid-cols-[1.2fr_1fr_1.2fr] items-end gap-6 pb-4">
        {/* Left: logotype */}
        <div>
          <div
            className="headline"
            style={{
              fontSize: "clamp(2rem, 3.6vw, 3rem)",
              letterSpacing: "-0.04em",
              color: "var(--ink)",
            }}
          >
            Shoppin Quarterly
          </div>
          <div className="eyebrow mt-1">
            D2C AI Employee · v0 · Issue 01
          </div>
        </div>

        {/* Center: date */}
        <div
          className="headline-italic text-center"
          style={{
            fontSize: "clamp(0.95rem, 1.4vw, 1.2rem)",
            color: "var(--ink-soft)",
          }}
        >
          {today || " "}
        </div>

        {/* Right: tenant picker */}
        <div className="flex flex-col items-end gap-1">
          <label
            className="eyebrow"
            style={{ color: "var(--ink-soft)" }}
            htmlFor="tenant-picker"
          >
            Tenant
          </label>
          <select
            id="tenant-picker"
            value={selectedTenantId}
            onChange={(e) => onChange(e.target.value)}
            style={{
              fontFamily: "var(--font-fraunces), Georgia, serif",
              fontVariationSettings: '"opsz" 14, "SOFT" 50',
              fontStyle: "italic",
              fontSize: "1rem",
              color: "var(--ink)",
              background: "transparent",
              border: "none",
              borderBottom: "1px solid var(--rule)",
              padding: "2px 0",
              textAlign: "right",
              cursor: "pointer",
            }}
          >
            {tenants.length === 0 && (
              <option value="">— no tenants —</option>
            )}
            {tenants.map((t) => (
              <option key={t.tenant_id} value={t.tenant_id}>
                {t.slug}
              </option>
            ))}
          </select>
          {tid && (
            <button
              type="button"
              onClick={copy}
              className="font-mono"
              title={copied ? "Copied" : "Click to copy tenant_id"}
              style={{
                fontSize: "0.66rem",
                color: "var(--ink-soft)",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                letterSpacing: "0.05em",
                padding: 0,
              }}
            >
              {copied ? "COPIED ✓" : truncated}
            </button>
          )}
        </div>
      </div>

      <div style={{ borderTop: "2px solid var(--ink)" }} />
    </header>
  );
}
