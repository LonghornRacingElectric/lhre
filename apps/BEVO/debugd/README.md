# debugd

Smoke-test tool: connects to cand's IPC socket and pretty-prints decoded
snapshots to stdout. Run it when you want to know "is data flowing?"
without starting the dash or logger.

Frames use `ArtemisSensorData`; debug output identifies them by BEVO time.
