# Log, 2026-11-16 machine benchmark

Times are Central.

- **Mon 11/16, ~4:00 AM.** Black-ridge WSL: Ubuntu 24.04 with Jazzy already
  installed. `glxinfo` showed llvmpipe (CPU). `GALLIUM_DRIVER=d3d12` gave
  OpenGL 4.6 on the RTX 4070 SUPER. Seed 1 gate probe at 4:06: pass at 0.3 s,
  0.76x.
- **~4:08 AM.** WSL memory cap 6 GB to 16 GB, WSL restarted.
- **4:10 AM.** Gate campaigns started on black-ridge WSL and red-mesa (red-mesa
  checked idle first: no sims, runners or builds).
- **4:12 AM.** white-canyon VM campaign started (Ubuntu 24.04 server installed
  unattended under Hyper-V the same night).
- **4:14 AM.** blue-crest campaign started in the Colima container; Mac kept
  awake with `caffeinate`.
- **4:25 AM.** hil campaign started after Gray added `electric` to the docker
  group and installed the NVIDIA container toolkit; image built on hil.
- **4:28 to 5:08 AM.** All five finish, 18 of 18 each. Everything stopped for
  the night: containers, VM, WSL.
- **1:17 PM.** white-canyon WSL campaign started (WSL enabled by Gray, Ubuntu
  24.04 installed). The wrapper's `glxinfo` hung on the WSLg display from an
  SSH session; killed, the gate continued. `bench.sh` now queries headless EGL
  with a 10 s timeout instead.
- **~2:00 PM.** white-canyon WSL stopped: 4 laps at about 0.09x, 3 runs hung
  with `/clock` never advancing.
- **2:09 PM.** Four isolated campaigns started at once on hil (containers,
  `--network none`); **2:10 PM** four on black-ridge (`unshare` namespaces in
  WSL). Results pending.
