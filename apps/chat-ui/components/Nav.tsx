"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS: { href: string; label: string; hint?: string }[] = [
  { href: "/", label: "Chat", hint: "Cited Q&A" },
  { href: "/runs", label: "Agent Runs", hint: "Proposed actions" },
  { href: "/metrics", label: "Semantic Layer", hint: "Metrics & dims" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav
      style={{
        borderBottom: "1px solid var(--rule)",
        background: "var(--bg)",
      }}
    >
      <div
        style={{
          maxWidth: 1400,
          margin: "0 auto",
          padding: "0 clamp(1rem, 3vw, 2rem)",
          display: "flex",
          alignItems: "stretch",
          gap: 4,
          overflowX: "auto",
        }}
      >
        {ITEMS.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                padding: "10px 12px",
                borderBottom: active
                  ? "2px solid var(--accent)"
                  : "2px solid transparent",
                marginBottom: -1,
                color: active ? "var(--ink)" : "var(--ink-soft)",
                fontSize: 13,
                fontWeight: active ? 600 : 500,
                whiteSpace: "nowrap",
              }}
            >
              {item.label}
              {item.hint && (
                <span
                  className="font-mono"
                  style={{
                    fontSize: 10,
                    color: "var(--ink-dim)",
                    letterSpacing: "0.02em",
                  }}
                >
                  {item.hint}
                </span>
              )}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
