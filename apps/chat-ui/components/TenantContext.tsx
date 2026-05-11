"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  fetchTenants,
  DEFAULT_TENANT_ID,
  type Tenant,
} from "@/lib/api";

type Ctx = {
  tenants: Tenant[];
  tenantId: string;
  setTenantId: (id: string) => void;
  error: string | null;
};

const TenantCtx = createContext<Ctx | null>(null);
const STORAGE_KEY = "shoppin.tenant_id";

export function TenantProvider({ children }: { children: React.ReactNode }) {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenantId, setTenantIdRaw] = useState<string>(DEFAULT_TENANT_ID);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const saved =
      typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    if (saved) setTenantIdRaw(saved);

    fetchTenants()
      .then((ts) => {
        setTenants(ts);
        if (!saved && ts.length > 0) {
          const demo = ts.find((t) => t.slug === "demo") ?? ts[0];
          setTenantIdRaw(demo.tenant_id);
        }
      })
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "tenants fetch failed"),
      );
  }, []);

  const setTenantId = (id: string) => {
    setTenantIdRaw(id);
    if (typeof window !== "undefined") localStorage.setItem(STORAGE_KEY, id);
  };

  const value = useMemo(
    () => ({ tenants, tenantId, setTenantId, error }),
    [tenants, tenantId, error],
  );

  return <TenantCtx.Provider value={value}>{children}</TenantCtx.Provider>;
}

export function useTenant(): Ctx {
  const v = useContext(TenantCtx);
  if (!v) throw new Error("useTenant must be used inside TenantProvider");
  return v;
}
