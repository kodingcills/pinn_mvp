import { useEffect, useState, type ReactNode } from "react";

export type Loadable<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: T };

/** Load once per `key`; re-runs when the key changes. */
export function useLoad<T>(load: () => Promise<T>, key: string): Loadable<T> {
  const [value, setValue] = useState<Loadable<T>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    setValue({ state: "loading" });
    load().then(
      (data) => live && setValue({ state: "ready", data }),
      (e: unknown) => live && setValue({ state: "error", message: e instanceof Error ? e.message : String(e) }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return value;
}

export function Gate<T>({ value, children }: { value: Loadable<T>; children: (data: T) => ReactNode }) {
  if (value.state === "loading") return <p className="muted">Loading…</p>;
  if (value.state === "error") return <p className="note warn">Request failed: {value.message}</p>;
  return <>{children(value.data)}</>;
}

/** Footnote naming the artifact the numbers on screen come from. */
export function Source({ children }: { children: ReactNode }) {
  return <p className="source">Source: {children}</p>;
}

export const outcomeOf = (r: { disposition: string | null; bounded_success: boolean | null }) =>
  r.bounded_success == null ? r.disposition ?? "n/a" : `${r.disposition ?? "?"} · ${r.bounded_success ? "bounded ok" : "bounded fail"}`;
