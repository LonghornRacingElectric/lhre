import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

test("Vercel filters app changes across the full deployment range", () => {
  const root = mkdtempSync(join(tmpdir(), "lapdog-ignore-test-"));
  const app = join(root, "apps", "lapdog");
  const git = (...args) => {
    const result = spawnSync("git", args, { cwd: root, encoding: "utf8" });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout.trim();
  };
  const commit = () => {
    git("add", ".");
    git("commit", "-qm", "fixture");
    return git("rev-parse", "HEAD");
  };
  const check = (previous) => spawnSync(process.execPath, [join(app, "ignored-build.mjs")], {
    cwd: root,
    env: { ...process.env, VERCEL_GIT_PREVIOUS_SHA: previous },
    encoding: "utf8",
  }).status;
  try {
    mkdirSync(app, { recursive: true });
    copyFileSync(new URL("../ignored-build.mjs", import.meta.url), join(app, "ignored-build.mjs"));
    git("init", "-q");
    git("config", "user.email", "fixture@example.invalid");
    git("config", "user.name", "Fixture");
    const baseline = commit();
    writeFileSync(join(root, "unrelated.txt"), "outside app");
    const outside = commit();
    assert.equal(check(baseline), 0, "outside-only changes should skip");
    writeFileSync(join(app, "page.tsx"), "app changed");
    commit();
    writeFileSync(join(root, "unrelated.txt"), "another outside change");
    commit();
    assert.equal(check(outside), 1, "earlier app change in push must build");
    git("mv", "apps/lapdog/page.tsx", "moved-out.tsx");
    commit();
    assert.equal(check(outside), 0, "net app tree unchanged after moving file out");
    writeFileSync(join(app, "asset.svg"), "asset");
    const withAsset = commit();
    git("rm", "apps/lapdog/asset.svg");
    commit();
    assert.equal(check(withAsset), 1, "deleted app files must build");
    assert.equal(check(""), 1, "first deployment must build");
    assert.equal(check("0".repeat(40)), 1, "missing history must build");
    assert.equal(check("--invalid"), 1, "invalid baseline must build");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
