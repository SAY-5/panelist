/** Virtual clock. The simulation never reads the wall clock. */

export const EPOCH_MS = Date.UTC(2026, 8, 1, 9, 0, 0);

export class Clock {
  private t: number;

  constructor(startMs: number = EPOCH_MS) {
    this.t = startMs;
  }

  now(): number {
    return this.t;
  }

  advance(ms: number): void {
    if (ms < 0) throw new Error("clock cannot move backwards");
    this.t += ms;
  }

  set(ms: number): void {
    if (ms < this.t) throw new Error("clock cannot move backwards");
    this.t = ms;
  }
}

export function isoTime(ms: number): string {
  return new Date(ms).toISOString();
}
