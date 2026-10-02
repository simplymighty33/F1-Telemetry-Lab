"""Frozen desktop application entry point."""

from __future__ import annotations

import sys

from collector.gui import main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "audit-session":
        from collector.audit_session import main as audit_main
        sys.exit(audit_main(sys.argv[2:]))
    elif len(sys.argv) > 1 and sys.argv[1] == "compress-raw":
        from collector.compress_archive import main as compress_main

        sys.exit(compress_main(sys.argv[2:]))
    else:
        sys.exit(main())
