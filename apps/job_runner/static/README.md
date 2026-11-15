# Dashboard assets

The Node server serves these buildless HTML/CSS/JS files from an explicit
allowlist. No frontend package installation or CDN access is required. The
page polls the authenticated same-origin API, labels stale data, and renders
job names and output with textContent. It does not accept shell commands or
hold the worker's SSH key. Verify desktop and narrow layouts when editing it.

A failed state refresh persists the offline state until the next successful
refresh, including across template changes. Mutation controls remain disabled.
History refresh restores keyboard focus by run ID. Non-CPU templates disable
the CPU duration input and submit a bounded default. Browser tests use a stub
DOM, fetch and timers: `node --test apps/job_runner/browser.test.mjs`.

When the SSH request queue is saturated, a previously connected snapshot stays
connected with a cached-status label. Controls remain usable and requests wait
for the bounded transport capacity; overflow is reported as busy, not offline.


The launch form separates installed autonomy jobs from infrastructure checks.
The worker catalog supplies titles, descriptions, installed revisions and typed
parameter fields. Polling preserves edited values when the catalog is unchanged.
A catalog upgrade resets the form to the newly installed definition. Campaign
history uses the saved definition's title and revision, with authenticated links
to metrics, logs and binary bag files after completion. File downloads stream
individually; the compact archive remains a separate option.
