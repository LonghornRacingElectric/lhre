---
name: orchestrate-subagents
description: Plan and run LHRe simulation work as a small team of subagents that work in parallel. Use when a study, sweep, review, or doc task splits into parts that do not depend on each other, or when the user says "team", "subagents", "parallelize", or "spawn agents". Covers checking the person's AI plan and usage first, sizing the team to it, choosing roles, briefs, file ownership, and integration.
---

# Orchestrate subagents

A coordinator agent splits the work, gives each part to a subagent, and
puts the results together. Use subagents when there is usage for them. Fit
the team to the person and the task. There is no fixed team.

## Rules

1. **Ask about the plan first.** People on the team have different AI
   plans and usage limits. Some have much less than others. Ask which plan
   the person uses and how much of their limit is left this week. Do not
   guess. If they do not know, start small.
2. **If there is usage, use it.** Run independent parts in parallel. Do not
   do serial work that subagents can do at the same time.
3. **Size the team to the usage.**

   | Usage left | Team |
   | ---------- | ---- |
   | Low | No subagents, or one for a read-only search or review. Do the rest yourself. |
   | Medium | Two or three subagents on parts that do not depend on each other. |
   | High | One subagent for each independent part, plus an independent reviewer. |

   When usage is low, tell the person the plan and the expected cost before
   you start. When it is high, start.
4. **Choose roles for this task.** Do not copy a team from an earlier task.
   Pick only the roles the work needs. Common roles:
   - first-principles reviewer: checks the physics and the model against
     hand calculations, read-only;
   - simplifier: finds code, cases and text to cut without changing the
     result, read-only;
   - writer: writes one README, note, or doc;
   - infra or skill author: changes tools or docs on its own branch;
   - skeptic: argues the other side of the result and lists what was not
     modeled.
5. **Parallelize the work, not the sim.** A study run uses all the CPUs
   that Docker has. Run one sim at a time, from the coordinator. A subagent
   runs Docker only when the brief says so.
6. **Give each file one owner.** Reviewers do not edit. Each writer owns
   its files. Work for a separate PR goes on its own branch and worktree.
7. **Write a complete brief.** A subagent sees none of the conversation.
   Each brief states:
   - the decision the work informs;
   - the files to read and the files it may change;
   - the skills to load (for example `study-design`,
     `vehicle-dynamics-first-principles`, `bobsim-boundary`);
   - the rules: writing style, no attribution, BobSim is a black box;
   - the output: format, length limit, and what "done" means.
8. **Check what comes back.** A subagent report is a claim, not evidence.
   Check each finding against the files or the run output before you act
   on it or report it. Agreement between agents is not validation.
9. **Keep integration with the coordinator.** The coordinator applies
   fixes, runs the sim, merges branches, and reports to the person.
10. **Report short.** Tell the person which roles ran, what each found, and
    what changed. Do not paste subagent transcripts.

## Method

1. Write the decision and list the parts of the work.
2. Mark which parts depend on others. Only independent parts run in
   parallel.
3. Ask about the plan and usage (rule 1). Size the team (rule 3).
4. Write the briefs. Start the subagents in one message so they run at the
   same time.
5. While they run, do the coordinator work: start the sim, prepare the
   integration.
6. Check each result (rule 8). Apply the fixes. Rerun the sim if a fix
   changes the model.
7. Report to the person.

## Checks before you report

- The person's plan and usage were asked, or the reason to skip is stated.
- No two subagents changed the same file.
- Each finding you report was checked against a file or a run output.
- Only one sim ran at a time.
- The report names the roles and what each changed.
