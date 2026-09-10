import { ReactNode, useEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "./format";

/**
 * Counts from 0 to `target` once `target` is known. Uses requestAnimationFrame
 * timestamps rather than a wall-clock read, and settles immediately when the
 * visitor asks for reduced motion.
 */
export function useCountUp(target: number | null, durationMs = 1100): number {
  const [value, setValue] = useState(0);
  const frame = useRef(0);

  useEffect(() => {
    if (target === null) return;
    if (prefersReducedMotion() || durationMs <= 0) {
      setValue(target);
      return;
    }
    let start: number | null = null;
    const step = (ts: number) => {
      if (start === null) start = ts;
      const p = Math.min(1, (ts - start) / durationMs);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(target * eased);
      if (p < 1) frame.current = requestAnimationFrame(step);
      else setValue(target);
    };
    frame.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame.current);
  }, [target, durationMs]);

  return value;
}

export interface CounterProps {
  label: string;
  /** Null until the run that measures it has finished. */
  target: number | null;
  format?: (v: number) => string;
  /** Read out to assistive tech once the value settles. */
  spoken?: string;
  note?: ReactNode;
  accent?: boolean;
}

export function Counter({ label, target, format, spoken, note, accent = false }: CounterProps) {
  const live = useCountUp(target);
  const fmt = format ?? ((v: number) => Math.round(v).toLocaleString("en-US"));
  const settled = target !== null && Math.abs(live - target) < 0.5;
  return (
    <div className="counter">
      <span className="counter-label">{label}</span>
      <span className={`counter-value${accent ? " is-accent" : ""}`} aria-hidden="true">
        {target === null ? ".." : fmt(live)}
      </span>
      <span className="sr">{settled ? `${label}: ${spoken ?? fmt(target)}` : ""}</span>
      {note ? <span className="counter-note">{note}</span> : null}
    </div>
  );
}
