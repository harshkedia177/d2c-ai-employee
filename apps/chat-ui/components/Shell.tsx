"use client";

import { TenantProvider, useTenant } from "./TenantContext";
import { Masthead } from "./Masthead";
import { Nav } from "./Nav";

function ShellInner({ children }: { children: React.ReactNode }) {
  const { tenants, tenantId, setTenantId, error } = useTenant();
  return (
    <div style={{ minHeight: "100vh", background: "var(--bg)" }}>
      <Masthead
        tenants={tenants}
        selectedTenantId={tenantId}
        onChange={setTenantId}
        backendOk={!error}
      />
      <Nav />
      {error && (
        <div
          style={{
            maxWidth: 1400,
            margin: "0 auto",
            padding: "10px clamp(1rem, 3vw, 2rem)",
            background: "var(--danger-soft)",
            color: "var(--danger)",
            fontSize: 12.5,
            borderBottom: "1px solid var(--rule)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span className="dot" />
          Backend unreachable — {error}. Is uvicorn running on :8000?
        </div>
      )}
      {children}
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <TenantProvider>
      <ShellInner>{children}</ShellInner>
    </TenantProvider>
  );
}
