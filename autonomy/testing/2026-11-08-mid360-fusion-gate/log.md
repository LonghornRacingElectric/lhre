# Development log

Times are America/Chicago, 2026-11-08. Raw launch logs use Unix timestamps;
the runner's container clock is UTC.

- 00:01: Full ROS workspace tests ran with the repository and pinned BobSim
  source mounted in a separate headless test container. Earlier missing-model
  failures were environment setup issues; no dependency versions changed.
- 00:02:01: Started the default-timeout seed-1 development campaign.
- 00:02:54: Started Mid-360/stereo/fusion after the sim/truth baseline.
- 00:03:34: Safety tripped `LIDAR_STALE` in `DRIVING` at the 0.3 s default.
  Retained the run as failed. Short sensor-rate observations overlapped its
  termination and cannot establish a representative driving rate.
- 00:04:46: Started a separate diagnostic campaign with
  `safety_lidar_timeout_s:=1.5`, as documented for the Mac container. Kept the
  default-timeout campaign intact. This diagnostic cannot pass the official
  gate.
- 00:05:38: Started the diagnostic seed-1 run. The scorer records 33.6086 s,
  with zero cones/off-course events, and passed the existing scorer.
- 00:06:44: Started diagnostic acceleration. It reached `FINISHED`, with zero
  cones/off-course events and no safety trip. The recorded maximum speed
  after its first stop was 0.69 m/s, so the scorer correctly retained a
  failed `stops` check. No threshold was changed to pass it.
- The diagnostic scored one of two gate runs passing. Both campaigns and
  both truth baselines are retained in the committed results table.
