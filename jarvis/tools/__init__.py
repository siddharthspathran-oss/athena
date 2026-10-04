"""Tool packages. Importing this module registers every tool."""
from . import (android, apps, brief, browser, calc, files, knowledge, messaging, phonefiles, places, routines,
               ragazza, scheduler, screen, shell, study, system, taste, teams, todo, web, windows)  # noqa: F401
from .. import alarm, deadlines, health, inbox, rules, studyauto, upes  # noqa: F401,E402  (registers check_deadlines, the automation tools)

__all__ = [
    "system", "files", "web", "knowledge", "shell",
    "scheduler", "windows", "messaging", "study", "brief",
    "browser", "routines",
]
