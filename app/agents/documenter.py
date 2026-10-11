"""Documenter: the Excel report the user downloads and shares with their team (WTA-19).

Two sheets:
- **Summary**: site, dates, pages explored, scenario totals by priority and category (with
  what each one means), a table of the pages, and how to use the report.
- **Scenarios**: one row per scenario (ID, page, title, numbered steps, expected result,
  priority, category) plus empty Result and Notes columns for whoever runs them. Header
  row frozen, filters on, priorities color-coded.

`build_workbook` is a pure function over plain data (easy to test); `generate_report`
reads the job from the database and saves `reports/{job_id}.xlsx`. Every total is computed
from the same rows written to the Scenarios sheet, so the Summary cannot disagree with it.

Text from crawled pages and from the LLM is untrusted: control characters that would make
the file unreadable are stripped, and a value starting with "=" is stored as text, never
as a formula (openpyxl would otherwise write it as one: formula injection).
"""
import math
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell, MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from db import db_crud

PRIORITY_ORDER = ["critical", "high", "medium", "low"]
PRIORITY_MEANING = {
    "critical": "A core flow is blocked or there is a security or data risk. Run these first.",
    "high": "Important negative paths and validations on core flows.",
    "medium": "Secondary flows and validations on non-core forms.",
    "low": "Cosmetic or minor usability issues.",
}
CATEGORY_ORDER = ["functional", "negative", "validation", "security", "usability", "accessibility"]
CATEGORY_MEANING = {
    "functional": "The main path does what it promises.",
    "negative": "Wrong input or misuse is handled correctly.",
    "validation": "Field rules: required, format, length, allowed values.",
    "security": "Authentication, access and data exposure.",
    "usability": "Clarity of messages, labels and flow.",
    "accessibility": "Labels, keyboard use and focus.",
}
RESULT_OPTIONS = ["Not run", "Pass", "Fail", "Blocked"]

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
SECTION_FONT = Font(bold=True, size=12, color="1F4E78")
TITLE_FONT = Font(bold=True, size=16, color="1F4E78")
LABEL_FONT = Font(bold=True)
MUTED_FONT = Font(italic=True, color="595959")
PRIORITY_STYLE = {
    "critical": (PatternFill("solid", fgColor="FFC7CE"), Font(bold=True, color="9C0006")),
    "high": (PatternFill("solid", fgColor="FCE4D6"), Font(bold=True, color="833C0B")),
    "medium": (PatternFill("solid", fgColor="FFF2CC"), Font(color="7F6000")),
    "low": (PatternFill("solid", fgColor="EDEDED"), Font(color="404040")),
}
THIN = Side(style="thin", color="BFBFBF")
GRID = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
TOP_WRAP = Alignment(vertical="top", wrap_text=True)

# (header, width) of the Scenarios sheet; widths are in Excel character units
SCENARIO_COLUMNS = [
    ("ID", 9), ("Page", 34), ("Page type", 11), ("Title", 42), ("Steps", 62),
    ("Expected result", 46), ("Priority", 10), ("Category", 13), ("Result", 11), ("Notes", 30),
]
LINE_HEIGHT_PT = 15


@dataclass
class ReportJob:
    id: uuid.UUID
    url: str
    description: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None


@dataclass
class ReportPage:
    id: int
    url: str
    title: str | None
    page_type: str | None


@dataclass
class ReportScenario:
    code: str
    page_id: int
    title: str
    steps: list[str]
    expected_result: str
    priority: str | None
    category: str | None


# ------------------------------------------------------------------------------ helpers

def _clean(value: Any) -> Any:
    """Strip characters Excel refuses (they make the whole file fail to open)."""
    return ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value


def _put(cell: Cell | MergedCell, value: Any) -> Cell:
    """Write a value safely: no illegal characters, no accidental formulas, no timezones."""
    if isinstance(cell, MergedCell):
        raise TypeError(f"{cell.coordinate} is inside a merged range; write to its top-left cell")
    value = _clean(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)  # Excel has no timezones
    cell.value = value
    if isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"  # untrusted text, never a formula
    if isinstance(value, datetime):
        cell.number_format = "yyyy-mm-dd hh:mm"
    return cell


def _numbered(steps: Iterable[str]) -> str:
    return "\n".join(f"{n}. {step}" for n, step in enumerate(steps, start=1))


def _lines_needed(text: str, width: float) -> int:
    """Rough number of wrapped lines a text takes in a column of `width` characters."""
    chars_per_line = max(int(width * 1.15), 1)
    return sum(max(1, math.ceil(len(line) / chars_per_line)) for line in (text or "").split("\n"))


def _header_row(ws: Worksheet, row: int, headers: list[str], first_col: int = 1) -> None:
    for offset, header in enumerate(headers):
        cell = _put(ws.cell(row=row, column=first_col + offset), header)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, GRID
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _style_priority(cell: Cell | MergedCell, priority: str | None) -> None:
    if priority in PRIORITY_STYLE:
        cell.fill, cell.font = PRIORITY_STYLE[priority]


# ------------------------------------------------------------------------------ sheets

def _summary_sheet(
    ws: Worksheet,
    job: ReportJob,
    pages: list[ReportPage],
    scenarios: list[ReportScenario],
    generated_at: datetime,
) -> None:
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False
    for col, width in zip("ABCDE", (40, 52, 26, 14, 12)):
        ws.column_dimensions[col].width = width

    per_page = Counter(s.page_id for s in scenarios)
    by_priority = Counter(s.priority for s in scenarios)
    by_category = Counter(s.category for s in scenarios)
    uncovered = [p for p in pages if per_page[p.id] == 0]

    _put(ws["A1"], "Test scenario report").font = TITLE_FONT
    _put(ws["A2"], "Manual test scenarios designed for the pages explored on the site below.").font = MUTED_FONT

    facts = [
        ("Site", job.url),
        ("App description", job.description or "(not provided)"),
        ("Report generated (UTC)", generated_at),
        ("Pages explored", len(pages)),
        ("Test scenarios", len(scenarios)),
        ("Pages without scenarios", len(uncovered)),
    ]
    row = 4
    for label, value in facts:
        label_cell = _put(ws.cell(row=row, column=1), label)
        label_cell.font, label_cell.alignment = LABEL_FONT, Alignment(vertical="top")
        cell = _put(ws.cell(row=row, column=2), value)
        cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        if label == "Site":
            cell.hyperlink, cell.style = job.url, "Hyperlink"
        row += 1
    ws.row_dimensions[5].height = LINE_HEIGHT_PT * min(_lines_needed(job.description or "", 52), 6)

    def breakdown(title: str, label: str, order: list[str], counts: Counter, meaning: dict, color: bool, row: int) -> int:
        _put(ws.cell(row=row, column=1), title).font = SECTION_FONT
        row += 1
        _header_row(ws, row, [label, "What it means", "", "Scenarios"])
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        row += 1
        extra = sorted(k for k in counts if k not in order and k is not None)
        for key in [*order, *extra]:
            name = _put(ws.cell(row=row, column=1), key)
            if color:
                _style_priority(name, key)
            _put(ws.cell(row=row, column=2), meaning.get(key, "")).alignment = TOP_WRAP
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
            _put(ws.cell(row=row, column=4), counts.get(key, 0))
            for col in (1, 2, 3, 4):
                ws.cell(row=row, column=col).border = GRID
            row += 1
        _put(ws.cell(row=row, column=1), "Total").font = LABEL_FONT
        _put(ws.cell(row=row, column=4), len(scenarios)).font = LABEL_FONT
        return row + 2

    row = breakdown("Scenarios by priority", "Priority", PRIORITY_ORDER, by_priority, PRIORITY_MEANING, True, row + 1)
    row = breakdown("Scenarios by category", "Category", CATEGORY_ORDER, by_category, CATEGORY_MEANING, False, row)

    _put(ws.cell(row=row, column=1), "Pages explored").font = SECTION_FONT
    row += 1
    _header_row(ws, row, ["Page", "Title", "Page type", "Scenarios"])
    row += 1
    for page in pages:
        url = _put(ws.cell(row=row, column=1), page.url)
        url.hyperlink, url.style = page.url, "Hyperlink"
        url.alignment = Alignment(wrap_text=True, vertical="top")
        _put(ws.cell(row=row, column=2), page.title or "").alignment = TOP_WRAP
        _put(ws.cell(row=row, column=3), page.page_type or "").alignment = TOP_WRAP
        count = _put(ws.cell(row=row, column=4), per_page[page.id])
        count.alignment = Alignment(vertical="top")
        if per_page[page.id] == 0:
            count.font = Font(bold=True, color="9C0006")
        for col in (1, 2, 3, 4):
            ws.cell(row=row, column=col).border = GRID
        ws.row_dimensions[row].height = LINE_HEIGHT_PT * min(
            max(_lines_needed(page.url, 40), _lines_needed(page.title or "", 52)), 4
        )
        row += 1
    if uncovered:
        _put(ws.cell(row=row, column=1),
             "Pages with 0 scenarios could not be covered automatically; review them by hand.").font = MUTED_FONT
        row += 1

    row += 1
    _put(ws.cell(row=row, column=1), "How to use this report").font = SECTION_FONT
    tips = [
        "1. Open the Scenarios sheet. Each row is one test a person can run in a browser.",
        "2. Use the filter arrows in the header to show one page, priority or category.",
        "3. Run the critical and high scenarios first; they cover the flows that matter most.",
        "4. Follow the steps in order and compare what you see with the expected result.",
        "5. Record the outcome in the Result column (Pass, Fail, Blocked) and add notes.",
        "Values in <angle brackets> are placeholders: replace them with real test data.",
    ]
    for tip in tips:
        row += 1
        _put(ws.cell(row=row, column=1), tip)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)


def _scenarios_sheet(ws: Worksheet, pages: list[ReportPage], scenarios: list[ReportScenario]) -> None:
    ws.title = "Scenarios"
    page_by_id = {p.id: p for p in pages}
    _header_row(ws, 1, [header for header, _ in SCENARIO_COLUMNS])
    for index, (_, width) in enumerate(SCENARIO_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.row_dimensions[1].height = 22

    for row, scenario in enumerate(scenarios, start=2):
        page = page_by_id.get(scenario.page_id)
        steps = _numbered(scenario.steps)
        values = [
            scenario.code,
            page.url if page else "",
            page.page_type if page else "",
            scenario.title,
            steps,
            scenario.expected_result,
            scenario.priority or "",
            scenario.category or "",
            "",
            "",
        ]
        for col, value in enumerate(values, start=1):
            cell = _put(ws.cell(row=row, column=col), value)
            cell.alignment, cell.border = TOP_WRAP, GRID
        _style_priority(ws.cell(row=row, column=7), scenario.priority)
        # Explicit height: not every spreadsheet app auto-fits wrapped rows when opening a file
        lines = max(
            _lines_needed(str(values[i]), SCENARIO_COLUMNS[i][1]) for i in (1, 3, 4, 5)
        )
        ws.row_dimensions[row].height = LINE_HEIGHT_PT * min(lines, 25)

    last_row = max(len(scenarios) + 1, 2)
    ws.freeze_panes = "B2"  # header and ID stay visible while scrolling
    ws.auto_filter.ref = f"A1:{get_column_letter(len(SCENARIO_COLUMNS))}{last_row}"
    result = DataValidation(type="list", formula1=f'"{",".join(RESULT_OPTIONS)}"', allow_blank=True)
    result.prompt, result.promptTitle = "Pass, Fail, Blocked or Not run", "Result"
    ws.add_data_validation(result)
    result.add(f"I2:I{max(last_row, 500)}")


def build_workbook(
    job: ReportJob,
    pages: list[ReportPage],
    scenarios: list[ReportScenario],
    generated_at: datetime | None = None,
) -> Workbook:
    """Build the report from plain data. Totals come from `scenarios` itself."""
    wb = Workbook()
    summary = wb.active
    assert isinstance(summary, Worksheet)  # a new Workbook always starts with one worksheet
    _summary_sheet(summary, job, pages, scenarios, generated_at or datetime.now(timezone.utc))
    _scenarios_sheet(wb.create_sheet(), pages, scenarios)
    wb.active = 0
    wb.properties.title = f"Test scenario report - {job.url}"
    wb.properties.creator = "WebTestAgent"
    return wb


# --------------------------------------------------------------------------- database

def report_file(reports_dir: str | Path, job_id: uuid.UUID | str) -> Path:
    return Path(reports_dir) / f"{job_id}.xlsx"


def generate_report(db: Session, job_id: uuid.UUID, reports_dir: str | Path) -> Path:
    """Read a job's pages and scenarios and save its report as reports/{job_id}.xlsx."""
    job = db_crud.get_job(db, job_id)
    if job is None:
        raise ValueError(f"Job {job_id} does not exist")
    report_job = ReportJob(job.id, job.url, job.description, job.status.value, job.started_at, job.finished_at)
    pages = [ReportPage(p.id, p.url, p.title, p.page_type) for p in db_crud.get_pages_by_job(db, job_id)]
    scenarios = [
        ReportScenario(s.scenario_code, s.page_id, s.title, list(s.steps or []), s.expected_result, s.priority, s.category)
        for s in db_crud.get_scenarios_by_job(db, job_id)
    ]

    path = report_file(reports_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".xlsx.tmp")
    build_workbook(report_job, pages, scenarios).save(tmp)
    os.replace(tmp, path)  # atomic: a download never sees a half-written file
    return path
