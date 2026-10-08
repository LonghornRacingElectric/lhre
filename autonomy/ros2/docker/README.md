# Running the stack on macOS (Docker)

The supported platform is still native Ubuntu 24.04 ([GETTING-STARTED](../GETTING-STARTED.md)).
This container is the workaround for Macs: Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic
in Docker, with the desktop (RViz, Gazebo GUI) served to your browser via noVNC.
Runs at near-native speed on Apple Silicon (arm64 packages, software OpenGL for rendering).

## One-time setup

Already running Docker Desktop or OrbStack? Skip to `docker compose up`: the
`colima` lines below are only for setting up a daemon you don't have.

```bash
brew install colima docker docker-compose
# let docker find the compose plugin:
mkdir -p ~/.docker && cat > ~/.docker/config.json <<'EOF'
{ "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"] }
EOF
colima start --cpu 6 --memory 8 --disk 60
cd autonomy/ros2/docker
docker compose up -d --build
```

## Daily use

- Desktop in browser: **http://localhost:6080/vnc.html?autoconnect=true&resize=scale**
- Shell into the container: `docker exec -it -u ubuntu lhr-autonomy bash`
- The repo is bind-mounted at `~/autonomy` inside the container, so edit on the
  Mac with your normal editor and build/run inside the container:

```bash
cd ~/autonomy/ros2
./scripts/build.sh
./scripts/run_demo.sh                                  # kinematic demo
./scripts/run_gazebo_demo.sh track_style:=oval perception:=lidar
DISPLAY=:1 LIBGL_ALWAYS_SOFTWARE=1 ./scripts/rviz_demo.sh
```

(In a terminal opened inside the noVNC desktop, `DISPLAY` is already set.)

## Foxglove, which needs no desktop

Foxglove runs on the **Mac**, not in the container, so it needs neither noVNC
nor software OpenGL. That makes it the cheapest way to look at a run here, and
the only viewer that renders at native speed.

Once per Mac, import `foxglove/lhr_sim.json` into Foxglove and save the
layout. It sets **Scene → Mesh up-axis → Z-up** for Orion's CAD. Copy the
`layoutId` from the layout's share link and create `foxglove.local.env`
beside `foxglove.sh`:

```bash
FOXGLOVE_LAYOUT_ID=your_saved_layout_id
```

This file is gitignored because the ID belongs to your Foxglove account.
An exported `FOXGLOVE_LAYOUT_ID` overrides it for a single run. The launcher
requires a saved layout so it cannot silently restore an older Y-up scene.

From the Mac, one command. It starts the container if it is stopped, builds,
opens Foxglove with that saved layout and the localhost connection,
and launches with the bridge on, running until Ctrl-C rather than ending at
the first completed lap (it defaults to `enable_metrics:=false`, see
[lhr_demo](../src/lhr_demo/README.md#running-until-you-stop-it)). Extra
arguments go to the launch file:

```bash
cd autonomy/ros2/docker
./foxglove.sh                        # kinematic demo, lidar on
./foxglove.sh v_max:=12.0 seed:=7
```

Foxglove opens automatically at `ws://localhost:8765`; it retries while the
bridge starts. Port 8765 is published by `compose.yaml`, so there is no
container address to look up or mesh orientation to change each run. After
updating the repository layout, import and save it again (update the local
ID if the import creates a new layout).

To run the ROS stack by hand (without opening Foxglove automatically):

```bash
docker exec -it -u ubuntu lhr-autonomy bash
cd ~/autonomy/ros2 && ./scripts/build.sh
./scripts/run_demo.sh foxglove:=true lidar:=true
```

`build.sh` is not optional, which is why the script always runs it: it is what
installs `lhr_vehicle`'s mesh into `share/`, and that is what makes the car's
`package://` URI resolve. Skipping it gives a broken-asset error rather than
an older car.

After a reboot: `colima start && docker start lhr-autonomy`. If the
`Dockerfile` or `compose.yaml` changed since the image was built (e.g. after a
pull), `docker start` resumes the stale image, so run `docker compose up -d
--build` from this directory instead.

## Notes

- `ros2/build`, `ros2/install`, `ros2/log` are created by the container and are
  Linux-only artifacts; delete them if you ever switch this checkout to native Ubuntu.
- They are also bind-mounted, so they outlive the container. After recreating it
  the first build can fail with `[Errno 17] File exists` on a
  `resource_index/packages` symlink, because `--symlink-install` is pointing an
  existing link at a tree the old container made. `rm -rf build install log` and
  build again. Leave `ros2/data` alone, which is where bags and metrics live.
- Gazebo renders on CPU (llvmpipe), so expect 0.7-0.8x real-time factor. Fine for
  development; use a native Ubuntu box for anything performance-sensitive.
- The image layers a scipy fix over `tiryoh/ros2-desktop-vnc:jazzy` (see Dockerfile).
