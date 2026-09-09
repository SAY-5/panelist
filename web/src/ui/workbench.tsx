import { createContext, ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { createWorkbench, Workbench } from "../sim";

interface BenchContext {
  bench: Workbench;
  version: number;
  /** Re-render every section after a mutation of the shared platform. */
  bump: () => void;
  reset: () => void;
  expertId: string;
  setExpertId: (id: string) => void;
}

const Ctx = createContext<BenchContext | null>(null);

export function WorkbenchProvider({ children }: { children: ReactNode }) {
  const ref = useRef<Workbench>(createWorkbench());
  const [version, setVersion] = useState(0);
  const [expertId, setExpertId] = useState(() => ref.current.platform.experts[0]?.id ?? "");
  const bump = useCallback(() => setVersion((v) => v + 1), []);
  const reset = useCallback(() => {
    ref.current = createWorkbench();
    setExpertId(ref.current.platform.experts[0]?.id ?? "");
    setVersion((v) => v + 1);
  }, []);
  const value = useMemo(
    () => ({ bench: ref.current, version, bump, reset, expertId, setExpertId }),
    [version, bump, reset, expertId],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkbench(): BenchContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useWorkbench outside provider");
  return ctx;
}
