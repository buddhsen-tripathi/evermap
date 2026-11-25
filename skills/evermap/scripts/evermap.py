#!/usr/bin/env python3
"""Evermap toolkit. Run with --help for the list of commands."""

import sys

if sys.version_info < (3, 9):
    sys.stderr.write("evermap: Python 3.9 or newer is required\n")
    sys.exit(2)

sys.dont_write_bytecode = True

from evermap_lib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
