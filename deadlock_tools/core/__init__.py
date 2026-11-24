"""The editing core shared by the GUI and the command-line tools.

Every edit goes through one Workspace and one Transaction (core.workspace). Operations return Result objects; they
never print, exit or parse argv. The CLI modules are thin argparse front-ends over these functions and the GUI calls
them in-process.
"""
from deadlock_tools.core.workspace import Result, Transaction, Workspace  # noqa: F401
