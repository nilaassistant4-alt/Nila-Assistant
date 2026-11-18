#!/usr/bin/env python3
"""Single-file entry point so the unzipped folder is immediately runnable:

    python brain.py --help
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain.cli.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
