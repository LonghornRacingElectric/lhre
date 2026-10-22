import { spawnSync } from "node:child_process";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const directory = dirname(fileURLToPath(import.meta.url));
const previous = process.env.VERCEL_GIT_PREVIOUS_SHA;
let status = 1;
let reason = "No previous deployment commit; building.";

// Comparing with HEAD^ can miss app changes earlier in a multi-commit push.
if (previous && /^[0-9a-f]{40,64}$/i.test(previous)) {
  const diff = spawnSync(
    "git",
    ["diff", "--quiet", previous, "HEAD", "--", "."],
    { cwd: directory, encoding: "utf8", timeout: 10_000 },
  );
  if (diff.status === 0) {
    status = 0;
    reason = "LAPDOG unchanged since the last deployment; skipping build.";
  } else {
    reason = diff.status === 1
      ? "LAPDOG changed; building."
      : "Cannot compare deployment history; building.";
  }
}

console.log(reason);
// Vercel uses 0 to skip and 1 to build, the reverse of a typical success check.
process.exitCode = status;
