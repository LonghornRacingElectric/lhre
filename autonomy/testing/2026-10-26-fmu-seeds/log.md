# Test log

All times below are UTC on 2026-10-26 (local date: October 26, America/Chicago).

- Started OrbStack and the existing `lhr-autonomy` container. The workspace
  build completed: 16 packages, no failures.
- Ran the real compiled-FMU tests: 2 passed in 20.39 s, with one SciPy
  iteration-progress warning. No FMU rebuild or source change was needed.
- 22:58:11–22:59:04: seed 1, exit 0, `outcome=lap`.
- 22:59:04–22:59:57: seed 2, exit 0, `outcome=lap`. LiDAR simulator raised
  a subscription-conversion RuntimeError after lap acceptance and recorder
  shutdown. Retained the original recording and log.
- 22:59:57–23:00:51: seed 3, exit 0, `outcome=lap`.
- 23:00:51–23:01:42: seed 4, exit 0, `outcome=lap`.
- 23:01:42–23:02:36: seed 5, exit 0, `outcome=lap`.
- Audited all five bags: matching FMU manifests, nonempty LiDAR and both
  odometry streams, true lap-completion signals, mission DRIVING with no
  EMERGENCY, and no process errors before shutdown. Seed 2's teardown
  traceback is recorded separately in `audit.json`.
- Copied the five original metrics rows and the artifact manifest into
  this campaign folder. Raw recordings and logs remain gitignored.
  No GitHub comment, issue closure, merge, or SharePoint upload was made.
- Uploaded the full campaign to SharePoint after the initial results commit.
  Verified all 32 paths and individual file sizes against the local inventory:
  4,405,825,351 bytes total. Kept the local recordings. Added the SharePoint
  link and local checksum inventory to the write-up.
