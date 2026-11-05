"""Read literal workflow scripts for offline execution, not general YAML parsing."""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent


def read_run_block(workflow: str, step_name: str) -> str:
    lines = (ROOT / '.github' / 'workflows' / workflow).read_text(encoding='utf-8').splitlines()
    marker = f'      - name: {step_name}'
    matches = [index for index, line in enumerate(lines) if line == marker]
    if len(matches) != 1:
        raise ValueError(f'Expected one literal step named {step_name!r}')
    start = matches[0] + 1
    for index in range(start, len(lines)):
        if lines[index].startswith('      - name:') or re.match(r'^  [\w-]+:', lines[index]):
            break
        if lines[index] == '        run: |':
            script = []
            for line in lines[index + 1:]:
                if line and not line.startswith('          '):
                    break
                script.append(line[10:] if line else '')
            result = '\n'.join(script) + '\n'
            if '${{' in result:
                raise ValueError('Offline scripts must consume environment values, not GitHub expressions')
            return result
    raise ValueError(f'Step {step_name!r} has no literal run block')


if __name__ == '__main__':
    print(read_run_block(sys.argv[1], sys.argv[2]), end='')
