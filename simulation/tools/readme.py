import json
import re
import shutil
import sys
from pathlib import Path

BLOCK = re.compile(r"<!-- out:(?P<ref>[^ ]+) -->(?P<body>.*?)<!-- /out -->", re.S)
IMAGE = re.compile(r"!\[[^\]]*\]\((?P<name>[^)/]+\.(?:png|svg))\)")


def value(out_dir, ref):
    name, _, rest = ref.partition("#")
    path = out_dir / name
    if not rest:
        text = path.read_text(encoding="utf-8").rstrip("\n")
        return text if path.suffix == ".md" else f"```{path.suffix.lstrip('.')}\n{text}\n```"
    keys, _, spec = rest.partition("|")
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in keys.split("/"):
        data = data[int(key) if isinstance(data, list) else key]
    return format(data, spec).replace("-", "−") if isinstance(data, (int, float)) and spec else str(data)


def fill(readme, out_dir):
    def render(match):
        text = value(out_dir, match["ref"])
        body = f"\n\n{text}\n\n" if match["body"].startswith("\n") else text
        return f"<!-- out:{match['ref']} -->{body}<!-- /out -->"

    old = readme.read_text(encoding="utf-8")
    new = BLOCK.sub(render, old)
    if new != old:
        readme.write_text(new, encoding="utf-8", newline="\n")
    for match in IMAGE.finditer(new):
        figure = out_dir / match["name"]
        if figure.exists():
            shutil.copyfile(figure, readme.parent / match["name"])


if __name__ == "__main__":
    fill(Path(sys.argv[1]), Path(sys.argv[2]))
