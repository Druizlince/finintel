"""Allows `python -m fin <command>` - used instead of the fin.exe launcher, which Windows
Smart App Control may block because it is a newly generated, unsigned executable."""

from fin.cli import main

raise SystemExit(main())
