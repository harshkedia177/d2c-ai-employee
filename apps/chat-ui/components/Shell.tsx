"use client";

import { TenantProvider, useTenant } from "./TenantContext";
import { Masthead } from "./Masthead";
import { Nav } from "./Nav";

function ShellInner({ children }: { children: React.ReactNode }) {
  const { tenants, tenantId, setTenantId, error } = useTenant();
  return (
    <div style={{ minHeight: "100vh", background: "var(--paper)" }}>
      <Masthead
        tenants={tenants}
        selectedTenantId={tenantId}
        onChange={setTenantId}
      />
      <Nav />
      {error && (
        <div
          className="eyebrow"
          style={{
            marginLeft: "clamp(2rem, 8vw, 10rem)",
            marginRight: "clamp(2rem, 5vw, 5rem)",
            paddingTop: "1rem",
            color: "var(--danger)",
          }}
        >
          Cannot reach backend: {error}. Is uvicorn running on :8000?
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
