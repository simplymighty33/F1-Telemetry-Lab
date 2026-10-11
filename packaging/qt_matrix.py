"""Ten simulated-DPR cells. Source-data/physical-monitor claims stay separate."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for width, height in ((1366, 768), (1920, 1080)):
        for scale in ('1', '1.25', '1.5', '1.75', '2'):
            output = args.output / f'{width}-{scale}'
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale, PYTHONPATH=str(root))
            env.pop('QT_SCREEN_SCALE_FACTORS', None)
            env.pop('QT_FONT_DPI', None)
            result = subprocess.run([sys.executable, str(root / 'packaging' / 'qt_acceptance.py'),
                                     '--database', str(args.database.resolve()), '--output', str(output),
                                     '--width', str(width), '--height', str(height)], env=env,
                                    cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
            output.mkdir(parents=True, exist_ok=True)
            (output / 'test.log').write_text(result.stdout + result.stderr, encoding='utf-8')
            if result.returncode:
                raise RuntimeError(f'Matrix failed: {width} / {scale}, see {output}')
            row = json.loads((output / 'evidence.json').read_text(encoding='utf-8'))
            if row['DPR'] != float(scale) or not row['stop_visible'] or not row['source_unchanged'] or not all(row['fonts'].values()):
                raise AssertionError(f'Invalid evidence: {width} / {scale}')
            rows.append(row)
            print(f'{width}x{height} / DPR {scale}: {row["physical_size"]}, close / fonts / source passed', flush=True)
    (args.output / 'matrix.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
