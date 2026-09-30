"""Frozen iPadHub entry point; engine arguments are handled by app.main."""
from ipadhub.app import main


if __name__ == "__main__":
    raise SystemExit(main())
