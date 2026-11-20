"""Entry point for the portable Windows executable."""
import sys

from deadlock_tools.__main__ import main


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["gui"]))
