"""`python -m notes_cli`: the same as the `notes` command (the exam Stop hook runs it so)."""

import sys

from notes_cli.cli import main

if __name__ == "__main__":
    sys.exit(main())
