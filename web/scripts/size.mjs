// Payload budget for the built page: gzipped bytes of every JS chunk in dist/assets.
// The budget is a ceiling, not a target; the measured size at the time it was set is in the README.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { join } from "node:path";

const BUDGET = 73728;
const dir = join(process.cwd(), "dist", "assets");
const chunks = readdirSync(dir).filter((f) => f.endsWith(".js"));
if (chunks.length === 0) {
  console.error("no JS chunks in dist/assets; run the production bundle first");
  process.exit(2);
}
let total = 0;
for (const f of chunks) {
  const gz = gzipSync(readFileSync(join(dir, f))).length;
  total += gz;
  console.log(`${f}: ${gz} bytes gzipped`);
}
console.log(`total ${total} bytes gzipped, budget ${BUDGET}`);
process.exit(total <= BUDGET ? 0 : 1);
