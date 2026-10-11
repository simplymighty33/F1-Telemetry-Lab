"""Create a short-path, verified distributable without modifying build output."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collector import DISPLAY_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Built RaceTraceLab directory')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    name = 'racetrace' + DISPLAY_VERSION
    destination, archive = output / name, output / (name + '.zip')
    if not (source / 'RaceTraceLab.exe').is_file():
        raise ValueError('A complete Qt build is required')
    if destination.exists() or archive.exists():
        raise FileExistsError('Use a new output directory; existing packages are never replaced')
    if output == source or output.is_relative_to(source):
        raise ValueError('Package output must be outside the build source')
    output.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    shutil.copy2(ROOT / 'README.md', destination / 'README.md')
    names = {'qt-command-center-guide.md': 'Guide.md', 'qt-third-party-notices.md': 'Notices.md',
             'license-inventory.json': 'Licenses.json', 'native-library-inventory.json': 'Binaries.json'}
    for old, new in names.items():
        (destination / old).rename(destination / new)
    # Publication notices come from the current source, not an older build copy.
    shutil.copy2(ROOT / 'docs/qt-third-party-notices.md', destination / 'Notices.md')
    shutil.copy2(ROOT / 'docs/RELEASE_2.0.md', destination / 'Release.md')
    inventory = json.loads((destination / 'Licenses.json').read_text('utf-8'))
    seen = set()
    for row in inventory:
        old = row['file'].replace('\\', '/')
        old_path = (destination / old).resolve()
        if not old_path.is_relative_to(destination):
            raise ValueError('License path outside package')
        digest = hashlib.sha256(old_path.read_bytes()).hexdigest()
        # Shorten only names, retain full text and a reversible inventory.
        new = f"licenses/{row['package']}/L-{hashlib.sha256(old.encode()).hexdigest()[:12]}.txt"
        new_path = (destination / new).resolve()
        if not new_path.is_relative_to(destination) or new.casefold() in seen:
            raise ValueError('Invalid/duplicate license destination')
        seen.add(new.casefold())
        old_path.rename(new_path)
        row.update(file=new, original_file=old, sha256=digest)
    (destination / 'Licenses.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    for file in ('README.md', 'Guide.md', 'Notices.md'):
        path = destination / file
        text = path.read_text('utf-8')
        for old, new in names.items():
            text = text.replace('docs/' + old, new).replace(old, new)
        text = text.replace('docs/RELEASE_2.0.md', 'Release.md')
        path.write_text(text, encoding='utf-8')
    hashes = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in destination.rglob('*') if p.is_file()}
    if any(Path(n).suffix.lower() in ('.ttf','.ttc','.otf','.woff','.woff2') for n in hashes):
        raise ValueError('Third-party/system font redistribution is forbidden')
    longest = max(len(name + '/' + n) for n in hashes)
    if longest > 120:
        raise ValueError(f'Package paths too long: {longest}')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for relative in sorted(hashes):
            zipped.write(destination / relative, name + '/' + relative)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip():
            raise ValueError('Archive CRC validation failed')
        for relative, digest in hashes.items():
            if hashlib.sha256(zipped.read(name + '/' + relative)).hexdigest() != digest:
                raise ValueError(f'Archive hash mismatch: {relative}')
    proof = dict(archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                 file_count=len(hashes), max_archive_path=longest, files_sha256=hashes)
    (output / 'Package.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in proof.items() if k != 'files_sha256'}))


if __name__ == '__main__':
    main()
