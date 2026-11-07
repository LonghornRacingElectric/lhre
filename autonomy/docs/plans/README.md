# Plans

Working documents: integration plans, change logs, the year roadmap. Written as context for contributors and AI agents, not as onboarding material. Freshness varies, check dates.

| Doc | What it is |
|-----|-----------|
| [gazebo-integration.md](gazebo-integration.md) | Gazebo + LiDAR integration plan, current status, known issues |
| [camera-fusion.md](camera-fusion.md) | Phase 3 roadmap: Camera fusion, LiDAR fallback and planner wiring ([#100](https://github.com/LonghornRacingElectric/lhre/issues/100)) implemented; ROS checks pass and manual autocross/EKF visuals confirmed |
| [camera-lidar-fusion-design.md](camera-lidar-fusion-design.md) | Fusion contracts, side fallback, planner wiring and validation limits |
| [zed2i-stereo.md](zed2i-stereo.md) | Implemented first camera milestone: one ZED 2i, 2.1 mm lens, stereo images and calibration |
| [zed2i-stereo-implementation.md](zed2i-stereo-implementation.md) | Implementation plan for stereo geometry, ROS/RViz integration, and runtime validation |
| [on-car-architecture.md](on-car-architecture.md) | Target stack on the retrofit car: sensors, nodes, CAN, safety loop, and what swaps versus sim |
| [gazebo-change-notes.md](gazebo-change-notes.md) | Chronological change log (historical) |
| [retrofit-roadmap-2026-27.md](retrofit-roadmap-2026-27.md) | Year roadmap snapshot. The live version is the VMS Timeline in Notion |
| [mvp-pass-criteria.md](mvp-pass-criteria.md) | What the 10/28 software MVP gate run must show |

For onboarding, start at [ros2/GETTING-STARTED.md](../../ros2/GETTING-STARTED.md).
