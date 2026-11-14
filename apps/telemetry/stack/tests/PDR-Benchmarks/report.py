"""Turn local JSON measurements into compact PDR Markdown tables."""
import argparse
import json
import math
import statistics
from pathlib import Path


def timing(result, key):
    return f"{result[key]:.2f}" if key in result else "—"


def ingest_graphs(report, directory, prefix):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    rates = sorted({r["target_updates_per_second"] for r in report["results"]})
    for rate in rates:
        fig, axes = plt.subplots(4, 1, figsize=(11, 12), sharex=True, constrained_layout=True)
        for index, r in enumerate(x for x in report["results"] if x["target_updates_per_second"] == rate):
            color = plt.get_cmap("tab10")(index)
            label = r["pipeline"]
            timeline = r.get("timeline", [])
            for percentile, style in (("p50_ms", "-"), ("p95_ms", "--")):
                axes[0].plot([(b["start_s"] + b["end_s"]) / 120 for b in timeline], [b.get(percentile) if b.get(percentile) is not None else float("nan") for b in timeline], style, color=color, label=f"{label} {percentile[:3]}")
            points = r.get("backlog", [])
            axes[1].plot([p["elapsed_s"] / 60 for p in points], [p["pending"] for p in points], color=color, label=label)
            rates_by_bucket = {}
            for point in points:
                rates_by_bucket.setdefault(int(point["elapsed_s"] // 10), []).append(point)
            times, stored = [], []
            for bucket, samples in sorted(rates_by_bucket.items()):
                if len(samples) > 1:
                    first, last = samples[0], samples[-1]
                    times.append(bucket / 6)
                    stored.append((last["persisted"] - first["persisted"]) / max(.001, last["elapsed_s"] - first["elapsed_s"]))
            axes[2].plot(times, stored, color=color, label=label)
            queue_names = {name for p in points for name in (p.get("ingest") or {}).get("queues", {})}
            for name in sorted(queue_names):
                if name not in ("Orion", "bridge_queue"):
                    continue
                axes[3].plot([p["elapsed_s"] / 60 for p in points], [(p.get("ingest") or {}).get("queues", {}).get(name, {}).get("size", float("nan")) for p in points], color=color, label=f"{label}: {name}")
            for event in r.get("events", []):
                if event["kind"] in ("process_exit", "publisher_disconnect"):
                    for ax in axes:
                        ax.axvline(event["elapsed_s"] / 60, color=color, linestyle=":", alpha=.7)
                    axes[0].annotate(event["kind"], (event["elapsed_s"] / 60, .95), xycoords=("data", "axes fraction"), rotation=90, va="top", fontsize=8)
            previous_drops = 0
            for point in points:
                drops = sum((point.get("ingest") or {}).get("queue_overflows", {}).values())
                if drops > previous_drops:
                    axes[3].scatter(point["elapsed_s"] / 60, max([q["size"] for q in (point.get("ingest") or {}).get("queues", {}).values()] or [0]), color="red", marker="x", s=20)
                previous_drops = drops
        duration = max(r["duration_s"] for r in report["results"] if r["target_updates_per_second"] == rate) / 60
        end = max([p["elapsed_s"] / 60 for r in report["results"] if r["target_updates_per_second"] == rate for p in r.get("backlog", [])] or [duration])
        axes[2].plot([0, duration, duration, end], [rate, rate, 0, 0], color="black", linestyle=":", label=f"Offered {rate:g} updates/s; then drain")
        for ax in axes:
            ax.axvline(duration, color="grey", linestyle="--", alpha=.45)
            ax.set_xlim(left=0)
        for ax, ylabel in zip(axes, ("Visibility latency (ms)", "Sent but not persisted", "Persisted updates/s", "Ingest queue entries")):
            ax.set_ylabel(ylabel)
            ax.grid(alpha=.2)
            handles, _ = ax.get_legend_handles_labels()
            if handles:
                ax.legend(fontsize=8, loc="upper left")
            ax.set_ylim(bottom=0)
        axes[-1].set_xlabel("Elapsed time (minutes); drain time included")
        fig.suptitle(f"MQTT ingestion - {rate:g} Hz\nLatency grouped by send time; blank intervals have no observed commits", fontsize=13)
        for suffix in ("png", "svg"):
            path = directory / f"{prefix}-{rate:g}hz.{suffix}"
            fig.savefig(path, dpi=150)
            if suffix == "png":
                paths.append(path)
        plt.close(fig)
    return paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--graphs-dir", type=Path)
    args = parser.parse_args()
    lines = ["# PDR benchmarks", "", "Machine-specific measurements; consult each JSON report for configuration, failures and individual samples.", ""]
    failed = False
    for path in args.reports:
        report = json.loads(path.read_text())
        lines += [f"## {path.name}", "", f"Status: {report.get('correctness', 'completed' if report.get('completed') else 'incomplete')}", ""]
        if report.get("error"):
            lines += [f"Error: {report['error']}", ""]
        if "fixture" in report:
            fixture = report["fixture"]
            lines += [f"Fixture: {fixture['seconds']} seconds, {fixture['rows_per_database']:,} rows per database.", ""]
        results = report.get("results", [])
        if results and "pipeline" in results[0]:
            lines += ["| Pipeline | Case | Duration (s) | Load | Sent | Missing | Visibility p50/p95 (ms) | Sustainable |", "|---|---|---:|---:|---:|---:|---|---|"]
            for r in results:
                latency = r["commit_visibility_latency"]
                lines.append(f"| {r['pipeline']} | {r['case']} | {r['duration_s']} | {r['multiplier']}× | {r['sent']} | {r['missing']} | {timing(latency, 'p50_ms')} / {timing(latency, 'p95_ms')} | {r['sustainable']} |")
                failed |= bool(r["errors"] or r["missing"] or r["duplicates"])
        elif results:
            lines += ["| Backend | Window (s) | Signals | Aggregate | First (ms) | Cold burst p50/p95 (ms) | Warm burst p50/p95 (ms) | Errors |", "|---|---:|---|---|---:|---|---|---:|"]
            for r in results:
                if r.get("error") or not all(k in r for k in ("first", "cold_burst", "warm_burst")):
                    lines.append(f"| {r['variant']} | {r['window_seconds']} | - | {r['aggregate']} | - | - | - | failed: {r.get('error', 'missing phase')} |")
                    failed = True
                    continue
                first, cold, warm = r["first"], r["cold_burst"], r["warm_burst"]
                errors = sum(r[k]["errors"] for k in ("first", "cold_burst", "warm_burst"))
                lines.append(f"| {r['variant']} | {r['window_seconds']} | {r.get('selection', 'dashboard')} | {r['aggregate']} | {timing(first, 'p50_ms')} | {timing(cold, 'p50_ms')} / {timing(cold, 'p95_ms')} | {timing(warm, 'p50_ms')} / {timing(warm, 'p95_ms')} | {errors} |")
                failed |= bool(errors)
            if any("different_window_burst" in r for r in results):
                lines += ["", "| Backend | Window (s) | Position | Aggregate | Unique windows | Different-window p50 / p95 (ms) | Errors |", "|---|---:|---|---|---:|---|---:|"]
                for r in results:
                    if "different_window_burst" in r:
                        burst = r["different_window_burst"]
                        lines.append(f"| {r['variant']} | {r['window_seconds']} | {r.get('position', '-')} | {r['aggregate']} | {r['unique_windows']} | {timing(burst, 'p50_ms')} / {timing(burst, 'p95_ms')} | {burst['errors']} |")
                        failed |= bool(burst["errors"])
        lines.append("")
        if report.get("freshness"):
            lines += ["| Visibility source | Samples | p50 (ms) | p95 (ms) |", "|---|---:|---:|---:|"]
            for source in ("timescale", "gateway"):
                values = sorted(p[source + "_ms"] for p in report["freshness"] if source + "_ms" in p)
                if values:
                    lines.append(f"| {source} | {len(values)} | {statistics.median(values):.2f} | {values[math.ceil(len(values) * .95) - 1]:.2f} |")
            failed |= any(p["errors"] for p in report["freshness"])
            lines.append("")
        lines += ["- " + note for note in report.get("notes", [])]
        lines.append("")
        if args.graphs_dir and results and "pipeline" in results[0]:
            paths = ingest_graphs(report, args.graphs_dir, path.stem)
            lines += [f"![{image.stem}]({__import__('os').path.relpath(image, args.output.parent)})" for image in paths]
            lines.append("")
        failed |= bool(report.get("error"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n")
    print(args.output)
    if failed:
        print("Failures are present; do not interpret these as successful speedup measurements.")


if __name__ == "__main__":
    main()
