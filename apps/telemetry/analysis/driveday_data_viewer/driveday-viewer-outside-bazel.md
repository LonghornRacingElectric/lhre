# ADR: The drive day viewer is a local Next.js app, outside Bazel

- **Status:** Accepted
- **Date:** 2026-10

## Context

After a drive day the team wants to see laps, sectors, corners, braking
zones, battery temperatures and the energy plan for a run, scrubbed like a
video. That began as one HTML file built against a session export from
`viewer_tool`'s trackside-live page. It grew past what one file can be
maintained as: three thousand lines in one closure, no tests, no types.

The viewer has no server. A 15 MB export is parsed in the browser, stored
in IndexedDB, and never leaves the machine. Presubmit runs `bazel test //...`
on Linux and Windows for every PR, and the telemetry Python lock files are
not set up for npm.

## Decision

- The viewer lives at `apps/telemetry/analysis/driveday_data_viewer/`. It is
  a Next.js (app router) and TypeScript project built with npm. It is not a
  Bazel package tree and is listed in `.bazelignore`.
- It is a local tool for now: `npm run dev`, then import an export. No
  deployment, no server routes, no database. Notes, cones and setup live in
  the browser next to the run they describe.
- The analysis (lap re-cutting, sectors, corner and braking-zone detection,
  energy model) is plain TypeScript in `src/lib/` with vitest tests. React
  only draws it.
- `npm run lint`, `typecheck`, `test` and `build` are the checks. They are
  not part of presubmit.
- `node_modules` is ignored by the docs build in `mkdocs.yml`, because
  `mkdocs-simple` turns every `*.md` it finds into a page.

## Alternatives considered

- **Keep the single HTML file.** It works, but nobody can change it with
  confidence, and a type error shows up on a drive day.
- **Build it with Bazel (`rules_js`).** It would pin the toolchain and make
  the build hermetic, but every contributor would pay for it on every PR for
  a tool one person runs locally. ADR-009 and ADR-012 made the same call for
  ROS 2 and simulation.
- **Add it as a page to `viewer_tool`.** `viewer_tool` is part of the
  telemetry stack's Bazel build and ships as a container image. The viewer
  has a different release rhythm and no need for the stack.
- **Host it on the telemetry server.** Possible later. It needs an auth and
  storage story first, and today nobody has asked for one.

## Consequences

- `bazel test //...` neither builds nor tests the viewer. Anyone changing it
  runs the four npm scripts by hand.
- Node 20 or newer and npm are needed in that folder, on any OS.
- The export format is owned by `viewer_tool`. If its `SessionExport` shape
  changes, `src/lib/types.ts` and `src/lib/convert.ts` change with it, and
  the synthetic fixture in `tests/fixtures/` shows what breaks.
- Notes in the browser do not follow a person to another machine. Sharing
  them would need a server, which is the follow-up if the team wants it.
