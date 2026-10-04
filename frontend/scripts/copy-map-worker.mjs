/** Put MapLibre's worker where the browser can fetch it.
 *
 * MapLibre finds its own worker through `import.meta.url` at runtime, which
 * works when the library is served as plain ES modules and does not when a
 * bundler has rewritten that value — webpack included. The library then asks
 * for an empty URL and the map comes up blank with "Worker failed to load",
 * which looks like a broken page rather than a resolution problem.
 *
 * So the two files are copied into `public/` and pointed at explicitly. They
 * are copied rather than committed because a vendored copy of a dependency's
 * build output goes stale silently: this way `npm install` of a new MapLibre
 * is picked up on the next start, and the worker can never disagree with the
 * library that loads it.
 *
 * The worker imports its sibling shared chunk by relative path, so both files
 * go, and they have to keep their names.
 */

import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FILES = ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"];

export function copyMapWorker(root = dirname(dirname(fileURLToPath(import.meta.url)))) {
  const from = join(root, "node_modules", "maplibre-gl", "dist");
  const to = join(root, "public", "maplibre");
  try {
    mkdirSync(to, { recursive: true });
    for (const file of FILES) copyFileSync(join(from, file), join(to, file));
    return true;
  } catch (error) {
    // Not fatal: every other page works, and the map says for itself that it
    // could not start. Failing the build over it would be worse.
    console.warn(`[maplibre] could not stage the worker: ${error.message}`);
    return false;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) copyMapWorker();
