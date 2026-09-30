// Pre-renders display math so pages ship no math JavaScript.
//
// Scans content/ for blocks written as
//
//   <div class="math">
//   $$ ...TeX... $$
//   </div>
//
// renders each with KaTeX, and writes data/math.json as a list of
// ["$$...TeX...$$", "<rendered HTML>"] pairs. templates/page.html swaps each
// source string for its HTML on pages that set `[extra] math = true`.
// Run `npm run math` after adding or editing a formula; CI runs it on deploy.
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import katex from "katex";

const walk = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return walk(path);
    return path.endsWith(".md") ? [path] : [];
  });

const block = /<div class="math">\s*(\$\$[\s\S]*?\$\$)\s*<\/div>/g;
const rendered = new Map();

for (const file of walk("content")) {
  for (const [, source] of readFileSync(file, "utf8").matchAll(block)) {
    if (rendered.has(source)) continue;
    try {
      rendered.set(
        source,
        katex.renderToString(source.slice(2, -2), { displayMode: true, throwOnError: true }),
      );
    } catch (err) {
      console.error(`${file}: ${err.message}`);
      process.exit(1);
    }
  }
}

const pairs = [...rendered].sort(([a], [b]) => a.localeCompare(b));
writeFileSync("data/math.json", JSON.stringify(pairs, null, 1) + "\n");
console.log(`rendered ${pairs.length} formula(s) into data/math.json`);
