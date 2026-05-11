"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS: { href: string; label: string }[] = [
  { href: "/", label: "Conversation" },
  { href: "/runs", label: "Agent Bench" },
  { href: "/metrics", label: "Metrics" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav
      style={{
        marginLeft: "clamp(2rem, 8vw, 10rem)",
        marginRight: "clamp(2rem, 5vw, 5rem)",
        paddingTop: "0.75rem",
        paddingBottom: "0.75rem",
        borderBottom: "1px solid var(--rule)",
      }}
    >
      <div className="font-mono" style={{ fontSize: "0.7rem", letterSpacing: "0.16em" }}>
        {ITEMS.map((item, i) => {
          const active = pathname === item.href;
          return (
            <span key={item.href}>
              {i > 0 && (
                <span style={{ color: "var(--ink-soft)", margin: "0 0.6rem" }}>·</span>
              )}
              <Link
                href={item.href}
                style={{
                  color: active ? "var(--ink)" : "var(--ink-soft)",
                  textTransform: "uppercase",
                  textDecoration: active ? "underline" : "none",
                  textUnderlineOffset: "4px",
                  textDecorationThickness: "1px",
                  textDecorationColor: "var(--ink)",
                }}
              >
                {item.label}
              </Link>
            </span>
          );
        })}
      </div>
    </nav>
  );
}
