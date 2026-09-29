"""Tool packages. Importing this module registers every tool."""
from . import (apps, brief, browser, files, knowledge, messaging, phonefiles, places, routines,
               ragazza, scheduler, screen, shell, study, system, taste, todo, web, windows)  # noqa: F401
from .. import deadlines  # noqa: F401,E402  (registers check_deadlines)

__all__ = [
    "system", "files", "web", "knowledge", "shell",
    "scheduler", "windows", "messaging", "study", "brief",
    "browser", "routines",
]
