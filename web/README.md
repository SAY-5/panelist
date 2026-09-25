# Panelist browser demo

A static page that runs the Panelist service layer in the browser. `src/sim/` is a
TypeScript port of `panelist/services/{routing,grading,attention,payouts,analytics,delivery}.py`
and `sim/{world,demo}.py` as they stood at service version 1.0.0 (`src/sim/port.ts` names the
version and the services), with a seeded PRNG and a virtual clock standing in for PostgreSQL
and the wall clock. Rubric versions (2.0.0), calibration and tiers (3.0.0), consensus and
adjudication (4.0.0) and the ops overview and tick (5.0.0) are not ported: a multi-graded task
delivers every approved grade here, and the summary block stops at the delivery line. No
backend, no network calls, no Docker.

## Commands

```bash
npm install
npm run dev        # vite dev server
npm run build      # typecheck, then a production build into dist/
npm run selfcheck  # the port's own assertions, the conformance replay, the stylesheet checks
npm run typecheck  # tsc --noEmit
npm run size       # gzipped JS in dist/ against the budget in scripts/size.mjs
```

## Layout

| Path | What it holds |
| --- | --- |
| `src/sim/platform.ts` | Claims, leases, reclaim, rubric validation, attention, reviews, payouts, aggregates, delivery |
| `src/sim/world.ts` | Seeded experts, tasks, golden checks, rate cards, grading behaviour |
| `src/sim/demo.ts` | The 500-task run as a generator, one observable event per step |
| `src/sim/summary.ts` | `formatSummary()`, the block `sim/demo.py` printed at 1.0.0 |
| `src/sim/port.ts` | The service version the port tracks and the list of what is not ported |
| `src/sim/prng.ts`, `clock.ts`, `sha256.ts` | sfc32 PRNG, virtual clock, vendored SHA-256 |
| `src/selfcheck.ts` | Node self-check: the port's own rules, the conformance replay against `../tests/fixtures/port_conformance.json`, and the stylesheet contrast and size floors |
| `src/ui/` | Page sections built on one shared in-memory platform |

## Rules the port keeps

- `src/sim/` never calls `Math.random`, `Date.now` or `eval`. A seed plus a start
  time fully determines a run, so the same seed always prints the same summary.
- Numbers shown on the page are produced by running the port in the page, not
  typed into markup. The port has its own PRNG, so its totals differ from the
  Python run quoted in the top-level README while every invariant holds:
  zero tag mismatches, every double-assignment attempt blocked, careless experts
  paused, withheld payouts kept out of the statement.
- The checksum on the delivery card is a real SHA-256 of the JSONL body,
  recomputed on every change. `stableStringify()` prints floats the way Python's
  `json.dumps` does, so a row serialised here is byte for byte the row the service exports.
- `tests/test_port_conformance.py` runs one fixed scenario through the PostgreSQL service and
  records every outcome in `tests/fixtures/port_conformance.json`; the selfcheck replays it and
  asserts the port reproduces each claim, grade, payout, total and the JSONL sha256. Regenerate
  with `PANELIST_WRITE_FIXTURE=1 uv run pytest tests/test_port_conformance.py` after an intended
  behaviour change on the service side.

## Deployment

`vercel.json` builds with Vite and serves `dist/`. Set the project root to `web/`.
