/** Seeded PRNG (sfc32) with the sampling helpers the world generator needs. */

export class Rng {
  private a: number;
  private b: number;
  private c: number;
  private d: number;

  constructor(seed: number) {
    // Mix the seed through splitmix32 so nearby seeds diverge immediately.
    let s = seed >>> 0;
    const next = () => {
      s = (s + 0x9e3779b9) >>> 0;
      let z = s;
      z = Math.imul(z ^ (z >>> 16), 0x21f0aaad);
      z = Math.imul(z ^ (z >>> 15), 0x735a2d97);
      return (z ^ (z >>> 15)) >>> 0;
    };
    this.a = next();
    this.b = next();
    this.c = next();
    this.d = next();
    for (let i = 0; i < 12; i++) this.next();
  }

  /** Uniform float in [0, 1). */
  next(): number {
    const t = (((this.a + this.b) >>> 0) + this.d) >>> 0;
    this.d = (this.d + 1) >>> 0;
    this.a = this.b ^ (this.b >>> 9);
    this.b = (this.c + (this.c << 3)) >>> 0;
    this.c = (this.c << 21) | (this.c >>> 11);
    this.c = (this.c + t) >>> 0;
    return t / 4294967296;
  }

  /** Integer in [min, max], both inclusive. */
  int(min: number, max: number): number {
    return min + Math.floor(this.next() * (max - min + 1));
  }

  choice<T>(items: readonly T[]): T {
    const picked = items[Math.floor(this.next() * items.length)];
    if (picked === undefined) throw new Error("choice from empty list");
    return picked;
  }

  /** k distinct items, order randomized. */
  sample<T>(items: readonly T[], k: number): T[] {
    const pool = [...items];
    const out: T[] = [];
    for (let i = 0; i < k && pool.length > 0; i++) {
      const idx = Math.floor(this.next() * pool.length);
      const [item] = pool.splice(idx, 1);
      if (item !== undefined) out.push(item);
    }
    return out;
  }

  shuffle<T>(items: T[]): T[] {
    for (let i = items.length - 1; i > 0; i--) {
      const j = Math.floor(this.next() * (i + 1));
      const a = items[i];
      const b = items[j];
      if (a !== undefined && b !== undefined) {
        items[i] = b;
        items[j] = a;
      }
    }
    return items;
  }

  /** Hex string of n bytes, used for deterministic row ids. */
  hex(nBytes: number): string {
    let s = "";
    for (let i = 0; i < nBytes; i++) s += this.int(0, 255).toString(16).padStart(2, "0");
    return s;
  }

  /** Looks like a v4 UUID so exported rows resemble the real service. */
  uuid(): string {
    const h = this.hex(16);
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-4${h.slice(13, 16)}-a${h.slice(17, 20)}-${h.slice(20, 32)}`;
  }
}

/** Small deterministic string hash used to derive per-expert sub-seeds. */
export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
