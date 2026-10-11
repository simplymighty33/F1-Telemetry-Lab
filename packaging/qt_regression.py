"""Persist final regression output; uses isolated test files, not user captures."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,
                            text=True, encoding='utf-8', errors='replace')
    args.output.write_text(result.stdout + result.stderr, encoding='utf-8')
    print((result.stdout + result.stderr)[-2200:])
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
