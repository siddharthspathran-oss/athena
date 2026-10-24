"""Athena Deck - a board of real agent terminals for Windows (20 Oct 2026).

A clean-room take on the idea behind Symvibe (a Mac-only app): Claude Code,
Gemini (Antigravity) and PowerShell consoles as nodes on a pan-and-zoom board,
each showing whether it's working, resting or needs you; a composer that
launches a team at once; notes and live web tiles beside them; and sessions
that come back after a restart. Athena drives it by voice (jarvis/deck_link.py).

    consoles.py  real terminals (ConPTY via pywinpty) and their status
    board.py     workspaces and nodes, saved to data/board.json
    server.py    the local API + websockets (127.0.0.1:8430)
    hook.py      Claude Code's status hook for a console
    ui/          the board itself (xterm.js for the terminals)
"""
