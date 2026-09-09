import { EPOCH_MS } from "../sim";

export function money(cents: number): string {
  return "$" + (cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Virtual clock offset, printed like a stopwatch. */
export function tick(ms: number): string {
  const s = Math.max(0, ms - EPOCH_MS) / 1000;
  return `t+${s.toFixed(1)}s`;
}

export function hours(ms: number, now: number): string {
  return `${((ms - now) / 3600000).toFixed(0)}h`;
}

export function pct(v: number | null): string {
  return v === null ? "n/a" : `${(v * 100).toFixed(1)}%`;
}

export function short(id: string): string {
  return id.slice(0, 8);
}

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
