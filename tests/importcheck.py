"""Find names used without being imported or defined - the bug that only
crashes when that line finally runs (15 Oct 2026: `time` in registry.py and
phone.py, both caught by hand).

    python tests/importcheck.py

For every module: a common module name (time, re, json, ...) or a name from
`from x import y` that is read somewhere must be bound in that scope or an
enclosing one - imported, assigned, a parameter, a def/class, a loop or
`with`/`except` target. Stdlib only (ast); no third-party checker needed.
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WATCH = {"time", "threading", "re", "json", "os", "sys", "subprocess", "math", "random", "shutil", "dt",
         "datetime", "Path", "asyncio", "traceback", "difflib", "uuid", "tempfile", "inspect", "hashlib",
         "base64", "html", "urllib", "socket", "struct", "wave", "io", "glob", "itertools", "functools",
         "collections", "ctypes", "np", "cfg", "DATA", "load_json", "save_json", "tool", "Any"}


def bound_names(node: ast.AST) -> set[str]:
    """Names bound directly in this scope (not inside nested functions/classes)."""
    out: set[str] = set()

    def visit(n, top=False):
        if not top and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if not isinstance(n, ast.Lambda):
                out.add(n.name)
            return
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
        elif isinstance(n, ast.MatchAs) and n.name:
            out.add(n.name)
        for c in ast.iter_child_nodes(n):
            visit(c)

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            out.add(a.arg)
        if node.args.vararg:
            out.add(node.args.vararg.arg)
        if node.args.kwarg:
            out.add(node.args.kwarg.arg)
    visit(node, top=True)
    return out


def check(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    from_imported = {(a.asname or a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    watch = WATCH | from_imported
    problems: list[str] = []

    scopes_t = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)

    def own_nodes(node):
        """Nodes of this scope, not descending into nested scopes (yields those
        separately). Default values and decorators belong to the outer scope."""
        stack = list(ast.iter_child_nodes(node))
        while stack:
            n = stack.pop()
            if isinstance(n, scopes_t):
                yield ("scope", n)
                outer = list(getattr(n, "decorator_list", []))
                if not isinstance(n, ast.ClassDef):
                    outer += [d for d in n.args.defaults + n.args.kw_defaults if d is not None]
                stack.extend(outer)
                continue
            yield ("node", n)
            stack.extend(ast.iter_child_nodes(n))

    def walk(node, scopes):
        chain = scopes + [bound_names(node)]
        for kind, n in own_nodes(node):
            if kind == "scope":
                walk(n, chain)
            elif (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in watch
                  and not hasattr(builtins, n.id) and not any(n.id in s for s in chain)):
                problems.append(f"{path.relative_to(ROOT)}:{n.lineno}: '{n.id}' used but never imported/defined here")
    walk(tree, [])
    return sorted(set(problems))


if __name__ == "__main__":
    files = sorted((ROOT / "jarvis").rglob("*.py")) + [ROOT / "run.py"]
    found = []
    for f in files:
        if "graphify-out" in f.parts or "__pycache__" in f.parts:
            continue
        try:
            found += check(f)
        except SyntaxError as e:
            found.append(f"{f.relative_to(ROOT)}: SYNTAX ERROR {e}")
    for p in found:
        print(p)
    print(f"{len(files)} files checked, {len(found)} problem(s)")
    sys.exit(1 if found else 0)
