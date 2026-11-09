"""Launcher (double-click on Windows; also the PyInstaller entry point)."""
import sys

from checkbook.app import main

sys.exit(main())
