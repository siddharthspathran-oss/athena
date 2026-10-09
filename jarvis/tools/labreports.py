"""Lab reports by voice, through the lab report builder in C:\\claude code\\labreport.

"Athena, make my lab report for experiment 5.1" - the Word report is made
from the LMS manual (title, aim, apparatus, theory, empty observation tables)
and opens in Word. You type the readings into its tables, save and close;
"Athena, finish my lab report" fills the computed columns, adds a graph and
the conclusion from your numbers, and makes the PDF - in the same file, so
what you added in Word stays (9 Oct 2026; the first version had you type
readings into a text file).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from ..registry import tool

BUILDER = Path(r"C:\claude code\labreport")


def _builder():
    if str(BUILDER) not in sys.path:
        sys.path.insert(0, str(BUILDER))
    import labreport
    return labreport


def _latest() -> Path | None:
    drafts = sorted((BUILDER / "reports").glob("*.yaml"), key=lambda p: p.stat().st_mtime)
    return drafts[-1] if drafts else None


@tool(tags=["study"], timeout=240)
def lab_report(experiment: str = "") -> str:
    """Make a UPES lab report as a Word file. The first time for an experiment
    it is made from the LMS lab manual and opened in Word for the readings;
    asked again once readings are typed in, it is finished - computed columns,
    graph, conclusion - and saved as PDF too. Use for "make my lab report for
    experiment 5.1", "lab report for the wien bridge", "finish my lab report".

    experiment: which one - "experiment 5.1", "5.1", or what it is about ("wien bridge"); empty = the one last worked on
    """
    import yaml
    L = _builder()
    try:
        draft = L.draft_for(experiment) if experiment.strip() else _latest()
    except LookupError as e:
        return f"{e} Say 'get my LMS files' if the manual is new."
    if draft is None:
        return "Which experiment? Say, for example, 'make my lab report for experiment 5.1'."
    spec = yaml.safe_load(draft.read_text(encoding="utf-8")) if draft.exists() else {}
    report = Path(spec.get("report", "")) if spec.get("report") else None
    try:
        if report is None or not report.exists():
            if not draft.exists():
                draft = L.new(experiment)
            out = L.build(draft, final=False)
            os.startfile(str(out))
            return ("Your report is open in Word, with the theory written and empty observation tables. "
                    "Type your readings and the dates in, save and close it, then say 'finish my lab report'.")
        out = L.finish(draft)
    except (LookupError, RuntimeError) as e:     # tables still empty / the file is open in Word
        return str(e)
    pdf = out.with_suffix(".pdf")
    os.startfile(str(pdf if pdf.exists() else out))
    return (f"Done: {out.stem} is finished" + (" and saved as a PDF" if pdf.exists() else "") +
            ", in Documents\\Lab Reports. The theory and conclusion are drafts in your name - read them before you submit.")
