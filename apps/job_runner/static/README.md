# Dashboard assets

The Node server serves these buildless HTML/CSS/JS files from an explicit
allowlist. No frontend package installation or CDN access is required. The
page polls the authenticated same-origin API, labels stale data, and renders
job names and output with textContent. It does not accept shell commands or
hold the worker's SSH key. Verify desktop and narrow layouts when editing it.
