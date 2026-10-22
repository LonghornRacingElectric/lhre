import math
import re

import pytest
from matplotlib.text import Text

from tools.vcu_sil_report import (
    SECTIONS,
    TIRES,
    VARIABLES,
    peak_slip,
    report_pages,
    summary_rows,
    variable_filter,
    write_report,
)

REAR_LEFT = TIRES["RL"]


def _rows(*changes):
    return [{**dict.fromkeys(VARIABLES, 0.0), "time": float(index), **change} for index, change in enumerate(changes)]


def _texts(figure):
    return " ".join(text.get_text() for text in figure.findobj(Text))


def _summary(rows, label):
    panels = [panel for section in SECTIONS for panel in section.panels]
    return next(row for row in summary_rows(rows, panels) if row[0] == label)


def test_every_plotted_signal_reads_only_variables_in_the_filter():
    row = dict.fromkeys(VARIABLES, 1.0)
    for section in SECTIONS:
        for panel in section.panels:
            for _, value in panel.signals:
                value(row)


def test_variable_filter_matches_each_variable_and_no_other_name():
    pattern = re.compile(variable_filter())
    assert all(pattern.fullmatch(name) for name in VARIABLES)
    assert not pattern.fullmatch("velXY")
    assert not pattern.fullmatch("vcuXtau_fw")


def test_summary_row_gives_start_end_min_and_max_of_a_signal():
    rows = _rows({"velX": 15.0}, {"velX": 16.0}, {"velX": 12.0})
    assert _summary(rows, "Speed") == ("Speed", "m/s", 15.0, 12.0, 12.0, 16.0)


def test_motor_speed_is_reported_in_rpm():
    rows = _rows({"vcu.sens_motor_speed": 2 * math.pi * 10})
    assert _summary(rows, "Motor speed")[2:] == pytest.approx((600.0, 600.0, 600.0, 600.0))


def test_peak_slip_gives_the_slip_parts_at_the_largest_absolute_slip_ratio():
    rows = _rows(
        {f"{REAR_LEFT}.kappa": 0.01},
        {
            f"{REAR_LEFT}.kappa": -0.2,
            f"{REAR_LEFT}.slipModel.R0": 0.2,
            f"{REAR_LEFT}.slipModel.omega": 70.0,
            f"{REAR_LEFT}.slipModel.Vx": 15.0,
            f"{REAR_LEFT}.slipModel.Vsx": 1.0,
            f"{REAR_LEFT}.Fx": -500.0,
            f"{REAR_LEFT}.Fz": 800.0,
        },
        {f"{REAR_LEFT}.kappa": 0.1},
    )
    assert peak_slip(rows)["RL"] == pytest.approx(
        {"time": 1.0, "kappa": -0.2, "wheel_speed": 14.0, "vx": 15.0, "slip_velocity": -1.0, "fx": -500.0, "fz": 800.0}
    )


def test_report_of_a_failed_run_shows_fail_and_each_failure_on_the_first_page():
    first_page = _texts(report_pages(_rows({}, {}), ["The car sped up."], {"Case": "test"})[0])
    assert "✗ FAIL" in first_page
    assert "The car sped up." in first_page


def test_report_of_a_passing_run_has_one_page_with_plots_per_section():
    pages = report_pages(_rows({}, {}), [], {"Case": "test"})
    assert "✓ OK" in _texts(pages[0])
    assert [section.title in _texts(page) for section, page in zip(SECTIONS, pages)] == [True] * len(SECTIONS)
    assert [len(page.axes) for page in pages] == [len(section.panels) for section in SECTIONS]


def test_report_shows_the_run_details():
    assert "22c5056" in _texts(report_pages(_rows({}, {}), [], {"BobSim": "22c5056"})[0])


def test_report_of_a_run_with_no_rows_is_one_page_with_the_failure_and_no_plots():
    pages = report_pages([], ["The result has no rows."], {"Case": "test"})
    assert len(pages) == 1
    assert "The result has no rows." in _texts(pages[0])
    assert pages[0].axes == []


def test_write_report_writes_a_pdf_with_one_page_per_section(tmp_path):
    report_path = tmp_path / "report.pdf"
    write_report(report_path, _rows({}, {}), [], {"Case": "test"})
    content = report_path.read_bytes()
    assert content.startswith(b"%PDF")
    assert len(re.findall(rb"/Type /Page\b", content)) == len(SECTIONS)
