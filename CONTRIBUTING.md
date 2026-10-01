# Contributing and feedback

This is a hobby project. Bug reports and practical improvement suggestions are welcome through Issues. English and Chinese reports are both welcome.

Before proposing an implementation, please open an issue describing the workflow or reproducible problem. No open-source license has been selected yet, so do not assume public visibility grants unrestricted reuse or redistribution.

## Local checks

Use Python 3.10+ and run from the repository root:

```powershell
python -m unittest discover -q
```

Some GUI tests require Windows and a working desktop/Tk environment. A different environment may skip checks rather than provide equivalent Windows acceptance.

Start the GUI with `python -m collector.gui_main`. `build_windows.ps1` installs the optional PyInstaller build dependency and creates the full-folder Windows distribution.

## Project boundaries

- Keep Raw packet bytes as the primary archive; decoded and analytical results are rebuildable derivatives.
- Do not perform heavy analysis on the UDP receiver or Raw writer path.
- Preserve older archive readability and checkpoint/resume behavior.
- Treat Flashback rollback, frame joining, lap validity, and tyre provenance as testable data rules.
- Use bounded queues/caches and expose errors rather than silently accepting missing work.
- Do not promise that a test proves zero UDP loss or protection from sudden termination.

The source modules, tests, and historical design documents are included, but private recordings and machine-specific test outputs are not. Historical documents describe their own version; use the English 1.0 guide for current user-facing instructions.

## Privacy

Never commit recordings, real logs, private settings, credentials, or personal identifiers. `.gitignore` is a safety aid, not a substitute for reviewing files before committing. Use small synthetic fixtures in tests. Redact screenshots and error excerpts before sharing them publicly.
