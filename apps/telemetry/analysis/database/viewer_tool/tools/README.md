# LAPDOG video helper

The local muscle behind LAPDOG’s automatic playback copies.

`npm run video-helper` starts FFmpeg conversion on loopback port 43129.
`npm run dev` and `npm start` start the same helper alongside Next.js.
`ffmpeg-static` supplies the pinned executable; `LAPDOG_FFMPEG_PATH` can select
an existing installation. No shell commands or client-provided paths are run.

Local LAPDOG origins are accepted. For a hosted viewer, explicitly allow its
origin: `npm run video-helper -- --origin https://your-lapdog-host.example`.
`LAPDOG_VIDEO_ORIGINS` accepts a comma-separated list. Requests require an
allowed Origin and a helper-instance token. The service only binds 127.0.0.1.

Each selection automatically creates a single conversion job. Input streams
to a private temporary directory with backpressure, never into one giant
buffer. Maximum input is 8 GB and only one conversion runs at a time. Output
is H.264/yuv420p, at most 1920×1080 and 60 fps, with AAC audio and MP4 faststart.
CRF 24 targets a smaller playback copy; size reduction is not guaranteed for
already-efficient inputs. Frame rate may drop; playback timing is retained.

The original file is never modified. The client downloads the output into a
local browser URL and deletes the helper job. Cancel stops FFmpeg and removes
partial files. Expired jobs are removed after an hour without activity, and
normal helper shutdown removes remaining jobs. An abrupt OS/process kill can
leave `lapdog-video-*` directories in the system temp directory.

Run `npm run test:video-helper` for origin/auth, conversion and cancellation
tests using short generated fixtures, not private drive-day recordings.

See [LAPDOG](../README.md) for the complete import, sync and review workflow.

In viewer_tool, the development viewer uses port 3001. Production retains
Next.js's normal `PORT`/`--port` selection. Hosted users must run this helper
on their own computer with the viewer origin explicitly allowed; the helper
running beside a remote server cannot access their local files.
