import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import readme

SIM = Path(__file__).resolve().parents[1]


@pytest.fixture
def study(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "readme.json").write_text(
        json.dumps({"gain": 7.912, "law": "+50%", "nested": {"radius_m": 3.5}, "missing": None, "items": [1, None]}),
        encoding="utf-8",
    )
    (out / "nan.json").write_text('{"gain": NaN}', encoding="utf-8")
    (out / "table.md").write_text("| a |\n| - |\n| 1 |\n", encoding="utf-8")
    return tmp_path / "README.md", out


def _readme(path, *markers):
    text = "# Study\n\n" + "\n\n".join(f"Value <!-- out:{marker} -->old<!-- /out -->." for marker in markers) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def test_fills_values_tables_and_signs(study):
    path, out = study
    _readme(path, "readme.json#gain|+.1f", "readme.json#law", "readme.json#nested/radius_m|.2f", "table.md")
    readme.fill(path, out)
    text = path.read_text(encoding="utf-8")
    assert "<!-- out:readme.json#gain|+.1f -->+7.9<!-- /out -->" in text
    assert "<!-- out:readme.json#law -->+50%<!-- /out -->" in text
    assert "<!-- out:readme.json#nested/radius_m|.2f -->3.50<!-- /out -->" in text
    assert "| 1 |" in text


@pytest.mark.parametrize(
    ("marker", "words"),
    [
        ("readme.json#missing", "null"),
        ("readme.json#missing|.1f", "null"),
        ("readme.json#items", "null"),
        ("nan.json#gain|.1f", "NaN"),
        ("readme.json#absent", "no value"),
        ("readme.json#nested/absent", "no value"),
        ("readme.json#items/5", "no value"),
        ("nothere.json#gain", "does not exist"),
        ("nothere.md", "does not exist"),
    ],
)
def test_null_nan_or_missing_value_stops_the_fill(study, marker, words):
    path, out = study
    before = _readme(path, "readme.json#law", marker)
    with pytest.raises(readme.MissingValue) as error:
        readme.fill(path, out)
    assert words in str(error.value)
    assert marker.split("|")[0] in str(error.value)
    assert path.read_text(encoding="utf-8") == before


def test_command_exits_nonzero_with_a_message(study):
    path, out = study
    _readme(path, "readme.json#missing")
    result = subprocess.run(
        [sys.executable, str(SIM / "tools/readme.py"), str(path), str(out)], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "readme.json#missing" in result.stderr
    assert "Traceback" not in result.stderr
    assert "None" not in path.read_text(encoding="utf-8")
