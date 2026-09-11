"""TsBackup: compress a directory, number it by time, and keep sending it.

The package splits cleanly in two so the logic can be tested without a display:

  pure     config, log, archiver, transports, receiver, engine_core - no Qt,
           fully exercised by tests/selftest.py
  qt       engine, and everything under app/ - imported only when a GUI runs

Nothing in the pure half imports PySide6, so `python -m tests.selftest` runs on
a machine with no Qt libraries at all, which is where the compression, naming,
retention and transport-selection logic lives.
"""

__version__ = "0.1.0"
