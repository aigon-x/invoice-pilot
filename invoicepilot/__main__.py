"""Allow `python -m invoicepilot ...` without installing the package."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
