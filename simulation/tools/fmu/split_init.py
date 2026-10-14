import re
import sys
from pathlib import Path

CHUNK = 10000
SIGNATURE = re.compile(r'^void (\w+_read_input_fmu)\(MODEL_DATA\* modelData\)$')


def split(source: Path, chunk: int = CHUNK) -> int:
    lines = source.read_text(encoding='utf-8').splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if SIGNATURE.match(line.rstrip('\n')))
    name = SIGNATURE.match(lines[start].rstrip('\n')).group(1)
    if lines[start + 1].rstrip('\n') != '{':
        raise ValueError(f'{source}: expected "{{" after {name}')
    end = max(i for i, line in enumerate(lines) if line.rstrip('\n') == '}')
    body = lines[start + 2:end]
    for line in body:
        text = line.strip()
        if text in ('{', '}') or not (text.endswith(';') or text.startswith('/*')):
            raise ValueError(f'{source}: {name} is not a flat list of statements: {text!r}')
    if not body[-1].endswith('\n'):
        body[-1] += '\n'

    parts = [body[i:i + chunk] for i in range(0, len(body), chunk)]
    out = lines[:start]
    for k, part in enumerate(parts):
        out += [f'static void {name}_part{k}(MODEL_DATA* modelData)\n', '{\n', *part, '}\n\n']
    out += [f'void {name}(MODEL_DATA* modelData)\n', '{\n']
    out += [f'  {name}_part{k}(modelData);\n' for k in range(len(parts))]
    out += ['}\n', *lines[end + 1:]]
    source.write_text(''.join(out), encoding='utf-8')
    return len(parts)


if __name__ == '__main__':
    path = Path(sys.argv[1])
    print(f'{path.name}: split into {split(path)} functions of at most {CHUNK} lines')
