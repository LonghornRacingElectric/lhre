import math
import re
import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import matplotlib
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

Row = Mapping[str, float]
Signal = tuple[str, Callable[[Row], float]]

TIRES = {
    "FL": "chassis.detailedChassis.frAxleDW.leftTire",
    "FR": "chassis.detailedChassis.frAxleDW.rightTire",
    "RL": "chassis.detailedChassis.rrAxleDW.leftTire",
    "RR": "chassis.detailedChassis.rrAxleDW.rightTire",
}
TIRE_VARIABLES = ("kappa", "Fx", "Fz", "slipModel.omega", "slipModel.R0", "slipModel.Vx", "slipModel.Vsx")
VARIABLES = (
    "time",
    "velX",
    "accX",
    "yawVel",
    "vcu.acceleratorPedalCmd",
    "vcu.brakePedalCmd",
    "vcu.frSteerCmd",
    "vcu.sens_motor_speed",
    "vcu.sens_hv_bus_voltage",
    "vcu.sens_hv_bus_current",
    "vcu.tau_fw",
    "vcu.regen_fw",
    "vcu.tau_cmd_limited",
    "motor.motor.tau_act",
) + tuple(f"{prefix}.{name}" for prefix in TIRES.values() for name in TIRE_VARIABLES)

RAD_S_TO_RPM = 60 / (2 * math.pi)
RAD_TO_DEG = 180 / math.pi
INK = "#0b0b0b"
SECONDARY = "#52514e"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
GOOD = "#006300"
CRITICAL = "#d03b3b"
PAGE_SIZE_IN = (8.5, 11.0)
MARGIN_IN = 0.6
PLOT_LEFT_IN = 1.05
LINE_IN = 0.19
PANEL_TITLE_IN = 0.3
PANEL_GAP_IN = 0.08
MAX_PANEL_IN = 1.8
X_LABEL_IN = 0.4
WRAP_CHARACTERS = 110


@dataclass(frozen=True)
class Panel:
    name: str
    unit: str
    signals: tuple[Signal, ...]


@dataclass(frozen=True)
class Section:
    title: str
    caption: str
    panels: tuple[Panel, ...]
    per_tire: bool = False


def _column(name: str, scale: float = 1.0) -> Callable[[Row], float]:
    return lambda row: row[name] * scale


def _single(name: str, unit: str, column: str, scale: float = 1.0) -> Panel:
    return Panel(name, unit, ((name, _column(column, scale)),))


def _per_tire(name: str, unit: str, variable: str, scale: float = 1.0) -> Panel:
    return Panel(name, unit, tuple((tire, _column(f"{prefix}.{variable}", scale)) for tire, prefix in TIRES.items()))


SECTIONS = (
    Section(
        "Sim to VCU",
        "BobLib's driver model and plant make these. They are the firmware inputs, except the steer.",
        (
            Panel(
                "Pedal command",
                "0 to 1",
                (("Accelerator", _column("vcu.acceleratorPedalCmd")), ("Brake", _column("vcu.brakePedalCmd"))),
            ),
            _single("Steer command", "deg", "vcu.frSteerCmd", RAD_TO_DEG),
            _single("Motor speed", "rpm", "vcu.sens_motor_speed", RAD_S_TO_RPM),
            _single("Pack voltage", "V", "vcu.sens_hv_bus_voltage"),
            _single("Pack current", "A", "vcu.sens_hv_bus_current"),
        ),
    ),
    Section(
        "VCU to plant",
        "The firmware command, then BobLib's VCUCore limits, then the motor's torque lag.",
        (
            Panel(
                "Motor torque",
                "Nm",
                (
                    ("Firmware command", _column("vcu.tau_fw")),
                    ("After VCUCore limits", _column("vcu.tau_cmd_limited")),
                    ("Motor", _column("motor.motor.tau_act")),
                ),
            ),
            _single("Firmware regen limit", "Nm", "vcu.regen_fw"),
        ),
    ),
    Section(
        "Tire slip",
        "κ ≈ (R0·ω − Vx) / |Vx| after the relaxation length. The rear axle is driven.",
        (
            _per_tire("Slip ratio κ", "-", "kappa"),
            _per_tire("Slip velocity R0·ω − Vx", "m/s", "slipModel.Vsx", -1.0),
            _per_tire("Longitudinal force Fx", "N", "Fx"),
            _per_tire("Normal load Fz", "N", "Fz"),
        ),
        per_tire=True,
    ),
    Section(
        "Vehicle",
        "",
        (
            _single("Speed", "m/s", "velX"),
            _single("Longitudinal acceleration", "m/s²", "accX"),
            _single("Yaw rate", "deg/s", "yawVel", RAD_TO_DEG),
        ),
    ),
)


def variable_filter() -> str:
    return "|".join(re.escape(name) for name in VARIABLES)


def summary_rows(rows: Sequence[Row], panels: Sequence[Panel]) -> list[tuple[str, str, float, float, float, float]]:
    summary = []
    for panel in panels:
        for label, value in panel.signals:
            values = [value(row) for row in rows]
            name = panel.name if len(panel.signals) == 1 else f"{panel.name}: {label}"
            summary.append((name, panel.unit, values[0], values[-1], min(values), max(values)))
    return summary


def peak_slip(rows: Sequence[Row]) -> dict[str, dict[str, float]]:
    return {tire: _tire_at(prefix, max(rows, key=lambda row: abs(row[f"{prefix}.kappa"]))) for tire, prefix in TIRES.items()}


def _tire_at(prefix: str, row: Row) -> dict[str, float]:
    return {
        "time": row["time"],
        "kappa": row[f"{prefix}.kappa"],
        "wheel_speed": row[f"{prefix}.slipModel.R0"] * row[f"{prefix}.slipModel.omega"],
        "vx": row[f"{prefix}.slipModel.Vx"],
        "slip_velocity": -row[f"{prefix}.slipModel.Vsx"],
        "fx": row[f"{prefix}.Fx"],
        "fz": row[f"{prefix}.Fz"],
    }


def _number(value: float) -> str:
    return f"{0.0 if abs(value) < 1e-9 else value:.4g}"


def _text(figure: Figure, x_in: float, y_in: float, text: str, **style) -> None:
    figure.text(x_in, y_in, text, transform=figure.dpi_scale_trans, **{"va": "top", **style})


def _rule(figure: Figure, y_in: float, color: str) -> None:
    figure.add_artist(
        Line2D([MARGIN_IN, PAGE_SIZE_IN[0] - MARGIN_IN], [y_in, y_in], transform=figure.dpi_scale_trans, color=color, lw=0.6)
    )


def _draw_lines(figure: Figure, top_in: float, lines: Sequence[str], fontsize: float, color: str) -> float:
    step_in = fontsize * 1.5 / 72
    for index, line in enumerate(lines):
        _text(figure, MARGIN_IN, top_in - index * step_in, line, fontsize=fontsize, color=color)
    return top_in - len(lines) * step_in


def _draw_header(figure: Figure, top_in: float, failures: Sequence[str], run_details: Mapping[str, str]) -> float:
    status, color = ("✗ FAIL", CRITICAL) if failures else ("✓ OK", GOOD)
    _text(figure, MARGIN_IN, top_in, "VCU SIL run", fontsize=15, weight="bold", color=INK)
    _text(figure, PAGE_SIZE_IN[0] - MARGIN_IN, top_in, status, fontsize=15, weight="bold", color=color, ha="right")
    details = textwrap.wrap(" · ".join(f"{name} {value}" for name, value in run_details.items()), WRAP_CHARACTERS)
    top_in = _draw_lines(figure, top_in - 0.35, details, 8, SECONDARY)
    failure_lines = [line for failure in failures for line in textwrap.wrap(f"• {failure}", WRAP_CHARACTERS, subsequent_indent="  ")]
    return _draw_lines(figure, top_in - 0.05, failure_lines, 9, CRITICAL)


def _draw_table(
    figure: Figure, top_in: float, header: Sequence[str], body: Sequence[Sequence[str]], widths_in: Sequence[float]
) -> float:
    right_edges = [MARGIN_IN + sum(widths_in[: index + 1]) for index in range(len(widths_in))]
    x_positions = [MARGIN_IN, *right_edges[1:]]
    header_bottom = top_in - max(cell.count("\n") + 1 for cell in header) * LINE_IN
    for index, (cell, x_in) in enumerate(zip(header, x_positions)):
        _text(figure, x_in, header_bottom, cell, fontsize=8, weight="bold", color=SECONDARY, va="bottom", ha="right" if index else "left")
    _rule(figure, header_bottom - 0.03, BASELINE)
    for row_index, row in enumerate(body):
        row_top = header_bottom - 0.07 - row_index * LINE_IN
        for index, (cell, x_in) in enumerate(zip(row, x_positions)):
            _text(figure, x_in, row_top, cell, fontsize=8, color=INK, ha="right" if index else "left")
        _rule(figure, row_top - LINE_IN + 0.04, GRID)
    return header_bottom - 0.07 - len(body) * LINE_IN


def _summary_table(rows: Sequence[Row], panels: Sequence[Panel]) -> tuple:
    body = [(name, unit, *map(_number, values)) for name, unit, *values in summary_rows(rows, panels)]
    return ("Signal", "Unit", "Start", "End", "Min", "Max"), body, (2.9, 0.8, 0.9, 0.9, 0.9, 0.9)


def _tire_table(rows: Sequence[Row]) -> tuple:
    keys = ("time", "kappa", "wheel_speed", "vx", "slip_velocity", "fx", "fz")
    body = [(tire, *(_number(peak[key]) for key in keys)) for tire, peak in peak_slip(rows).items()]
    header = ("At peak |κ|", "t\n(s)", "κ\n(-)", "R0·ω\n(m/s)", "Vx\n(m/s)", "R0·ω − Vx\n(m/s)", "Fx\n(N)", "Fz\n(N)")
    return header, body, (1.0,) + (0.9,) * 7


def _draw_plots(figure: Figure, top_in: float, rows: Sequence[Row], panels: Sequence[Panel]) -> None:
    page_width_in, page_height_in = PAGE_SIZE_IN
    width_in = page_width_in - MARGIN_IN - PLOT_LEFT_IN
    slot_in = min(MAX_PANEL_IN, (top_in - MARGIN_IN - X_LABEL_IN) / len(panels))
    times = [row["time"] for row in rows]
    previous_labels: list[str] = []
    axes = []
    for index, panel in enumerate(panels):
        bottom_in = top_in - (index + 1) * slot_in + PANEL_GAP_IN
        height_in = slot_in - PANEL_TITLE_IN - PANEL_GAP_IN
        ax = figure.add_axes(
            (PLOT_LEFT_IN / page_width_in, bottom_in / page_height_in, width_in / page_width_in, height_in / page_height_in),
            sharex=axes[0] if axes else None,
        )
        axes.append(ax)
        series = [[value(row) for row in rows] for _, value in panel.signals]
        for (label, _), values, color in zip(panel.signals, series, SERIES_COLORS):
            ax.plot(times, values, color=color, lw=1.2, label=label)
        ax.set_title(f"{panel.name} ({panel.unit})", loc="left", fontsize=9, color=INK)
        ax.set_xlim(times[0], times[-1])
        low, high = min(map(min, series)), max(map(max, series))
        # Without this, matplotlib scales solver noise on a constant signal to the full plot height.
        minimum_span = 0.01 * max(abs(low), abs(high))
        if high - low < minimum_span:
            ax.set_ylim(low - minimum_span, high + minimum_span)
        ax.ticklabel_format(axis="y", useOffset=False)
        ax.grid(color=GRID, lw=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(color=BASELINE, labelcolor=SECONDARY, labelsize=8, labelbottom=index == len(panels) - 1)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(BASELINE)
        labels = [label for label, _ in panel.signals]
        if len(labels) > 1 and labels != previous_labels:
            ax.legend(
                loc="lower right",
                bbox_to_anchor=(1.0, 1.0),
                ncol=len(labels),
                frameon=False,
                fontsize=8,
                labelcolor=INK,
                borderaxespad=0.2,
                handlelength=1.2,
                columnspacing=1.0,
            )
        previous_labels = labels
    axes[-1].set_xlabel("Time (s)", fontsize=8, color=SECONDARY)


def _draw_section(figure: Figure, top_in: float, rows: Sequence[Row], section: Section) -> None:
    _text(figure, MARGIN_IN, top_in, section.title, fontsize=12, weight="bold", color=INK)
    top_in -= 0.28
    if section.caption:
        top_in = _draw_lines(figure, top_in, textwrap.wrap(section.caption, WRAP_CHARACTERS), 8.5, SECONDARY)
    table = _tire_table(rows) if section.per_tire else _summary_table(rows, section.panels)
    _draw_plots(figure, _draw_table(figure, top_in - 0.05, *table) - 0.2, rows, section.panels)


def report_pages(rows: Sequence[Row], failures: Sequence[str], run_details: Mapping[str, str]) -> list[Figure]:
    first_page = Figure(figsize=PAGE_SIZE_IN, facecolor="white")
    header_bottom = _draw_header(first_page, PAGE_SIZE_IN[1] - MARGIN_IN, failures, run_details)
    if not rows:
        return [first_page]
    pages = [first_page, *(Figure(figsize=PAGE_SIZE_IN, facecolor="white") for _ in SECTIONS[1:])]
    tops = [header_bottom - 0.3, *[PAGE_SIZE_IN[1] - MARGIN_IN] * (len(SECTIONS) - 1)]
    for page, top_in, section in zip(pages, tops, SECTIONS):
        _draw_section(page, top_in, rows, section)
    return pages


def write_report(path: Path, rows: Sequence[Row], failures: Sequence[str], run_details: Mapping[str, str]) -> None:
    # TrueType fonts keep the text searchable; no CreationDate keeps the file the same for the same run.
    with matplotlib.rc_context({"pdf.fonttype": 42}), PdfPages(path, metadata={"Title": "VCU SIL run", "CreationDate": None}) as pdf:
        for page in report_pages(rows, failures, run_details):
            pdf.savefig(page)
