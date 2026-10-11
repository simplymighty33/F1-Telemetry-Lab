"""Fetch exact upstream license resources for the local verification package."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name in ('LGPL-3.0-only.txt', 'GPL-3.0-only.txt', 'GPL-2.0-only.txt', 'LGPL-2.1-or-later.txt',
                 'Qt-GPL-exception-1.0.txt', 'Apache-2.0.txt', 'BSD-2-Clause.txt', 'BSD-3-Clause.txt', 'MIT.txt'):
        url = 'https://raw.githubusercontent.com/qt/qtbase/v6.11.2/LICENSES/' + name
        with urlopen(url, timeout=30) as response:
            body = response.read(200_000)
        if len(body) < 300 or body.lstrip().startswith(b'<') or b'\x00' in body:
            raise ValueError(f'Unexpected license response: {url}')
        (args.output / name).write_bytes(body)
        records.append({'file': name, 'url': url, 'sha256': hashlib.sha256(body).hexdigest()})
    (args.output / 'upstream-provenance.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    print(f'{len(records)} upstream license resources saved; all are unchanged bytes')


if __name__ == '__main__':
    main()
