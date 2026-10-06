"""LineScope.

Author: Mavs
Description: Self-contained, escaped, accessible source profiling reports.

"""

from __future__ import annotations

from base64 import b64encode
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from html import escape
from importlib.resources import files
from io import StringIO
from itertools import pairwise
import keyword
from math import isfinite, log1p
from pathlib import Path, PurePath
import tokenize
from typing import Any

from linescope.enums import Backend, NotebookCollection, SourceKind
from linescope.model import (
    BackendCapabilities,
    LineStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
    SparkExecution,
    SparkOperator,
)
from linescope.render.spark import (
    _operator_cost,
    _spark_steps,
    _SparkCost,
    _SparkPipeline,
    _SparkStep,
    _StepKind,
)


def _key(prefix: str, identity: str) -> str:
    """Create a stable report element identifier from an opaque identity.

    Hash the identity so paths and metadata do not become raw HTML identifiers.

    Parameters
    ----------
    prefix : str
        Prefix used to construct an identifier or nested display label.

    identity : str
        Opaque source or run identity used to derive a stable anchor.

    Returns
    -------
    str
        Stable HTML identifier derived from the opaque input identity.

    """
    return f"{prefix}-{sha256(identity.encode()).hexdigest()[:20]}"


def _source_link(identity: str, line: int = 1) -> str:
    """Create an internal link to a captured source line.

    Use the same stable source anchor format as rendered source pages.

    Parameters
    ----------
    identity : str
        Opaque source or run identity used to derive a stable anchor.

    line : int, default=1
        One-based source line used by the report link.

    Returns
    -------
    str
        Internal anchor pointing to the requested source line.

    """
    return f"#{_key('source', identity)}-L{line}"


def _spark_operator_id(execution_id: str, operator_id: str) -> str:
    """Identify a physical operator within its owning Spark action.

    Scope operator IDs to the action so repeated plans and child notebook
    executions retain independent navigation targets.

    Parameters
    ----------
    execution_id : str
        Opaque action identity owning the physical plan.

    operator_id : str
        Spark operator identity within that action's physical plan.

    Returns
    -------
    str
        Stable report anchor derived from the two opaque identities.

    """
    return f"{_key('spark', execution_id)}-{_key('operator', operator_id)}"


def _spark_source_link(location: SourceLocation | None, sources: dict[str, SourceUnit]) -> str:
    """Link an action's exact trigger to its captured source when available.

    Keep unavailable trigger snapshots unlinked instead of guessing from
    transformation references.

    """
    unit = sources.get(location.source_id) if location else None

    if unit is None or location is None or not 0 < location.line <= len(unit.source.splitlines()):
        return "Trigger source unavailable"

    path = _source_name(unit)
    label = f"{path}:{location.line}"
    return (
        f'<a href="{_source_link(location.source_id, location.line)}"'
        f' title="Go to triggering source line">{escape(label)}</a>'
    )


def _time(value: int | None) -> str:
    """Format nanoseconds with readable time units.

    Display unavailable measurements distinctly from measured zero.

    Parameters
    ----------
    value : int | None
        Measurement or serialized value to normalize or display.

    Returns
    -------
    str
        Formatted duration or the unavailable marker.

    """
    if value is None:
        return "—"

    if value < 1000:
        return "<1 µs" if value else "0 µs"

    if value < 1_000_000:
        return f"{value / 1000:,.1f} µs"

    if value < 1_000_000_000:
        return f"{value / 1_000_000:,.2f} ms"

    return f"{value / 1_000_000_000:,.2f} s"


def _total(values: list[int | None]) -> int | None:
    """Sum available measurements without inventing a value for unknown data.

    Return None when every supplied measurement is unavailable.

    Parameters
    ----------
    values : list[int | None]
        Measurements whose unavailable entries must remain distinguishable.

    Returns
    -------
    int | None
        Sum of available values, or None when no value is known.

    """
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _count(value: int | None) -> str:
    """Format an available execution or sampling count for display.

    Keep unavailable counts distinct from measured zero.

    Parameters
    ----------
    value : int | None
        Measurement or serialized value to normalize or display.

    Returns
    -------
    str
        Formatted count or the unavailable marker.

    """
    return "—" if value is None else f"{value:,}"


def _bytes(value: int | None, *, signed: bool = False) -> str:
    """Format a byte measurement using decimal size units.

    Use 1,000 bytes per KB and 1,000,000 bytes per MB. Preserve unknown values
    and optionally show a positive sign for byte changes.

    Parameters
    ----------
    value : int | None
        Measurement or serialized value to normalize or display.

    signed : bool, default=False
        Whether positive byte changes receive an explicit sign.

    Returns
    -------
    str
        Formatted byte measurement or the unavailable marker.

    """
    if value is None:
        return "—"

    sign = "+" if signed and value > 0 else ""

    for unit, size in (("GB", 10**9), ("MB", 10**6), ("KB", 10**3)):
        if abs(value) >= size:
            return f"{sign}{value / size:,.1f} {unit}"

    return f"{sign}{value:,} B"


def _walk(run: ProfileRun) -> list[ProfileRun]:
    """Flatten a profiling run tree in parent-first order.

    Include separate child runs and inline references for report navigation.

    Parameters
    ----------
    run : [ProfileRun]
        Profiling scope whose child runs are traversed.

    Returns
    -------
    list[ProfileRun]
        Profiling scopes in parent-first traversal order.

    """
    return [run, *[child for item in run.children for child in _walk(item)]]


def _tokens(source: str) -> dict[int, list[tuple[int, int, str]]]:
    """Collect source token spans for syntax highlighting.

    Preserve partial notebook source when tokenization cannot parse magic
    syntax.

    Parameters
    ----------
    source : str
        Source text or snapshot being inspected.

    Returns
    -------
    dict[int, list[tuple[int, int, str]]]
        Highlight spans grouped by one-based source line number.

    """
    spans: dict[int, list[tuple[int, int, str]]] = {}

    try:
        for token in tokenize.generate_tokens(StringIO(source).readline):
            kind = ""

            if token.type == tokenize.STRING:
                kind = "string"
            elif token.type == tokenize.COMMENT:
                kind = "comment"
            elif token.type == tokenize.NUMBER:
                kind = "number"
            elif token.type == tokenize.NAME and keyword.iskeyword(token.string):
                kind = "keyword"

            if kind:
                for number in range(token.start[0], token.end[0] + 1):
                    start = token.start[1] if number == token.start[0] else 0
                    end = token.end[1] if number == token.end[0] else 10**9
                    spans.setdefault(number, []).append((start, end, kind))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Notebook magics are not Python syntax; preserve the complete raw cell.
        pass

    return spans


def _code(text: str, spans: list[tuple[int, int, str]], stats: LineStats | None) -> str:
    """Render one escaped source line with token colors and resolved links.

    Split at token and symbol boundaries to preserve the original source
    spelling.

    Parameters
    ----------
    text : str
        Original text to escape, render, or mask.

    spans : list[tuple[int, int, str]]
        Character spans and token kinds used for source highlighting.

    stats : [LineStats] | None
        Available measurements and references for this source line.

    Returns
    -------
    str
        Escaped source HTML preserving token boundaries and links.

    """
    links = [] if stats is None else stats.calls
    boundaries = {0, len(text)}

    for start, end, _ in spans:
        boundaries.update((max(0, min(start, len(text))), max(0, min(end, len(text)))))

    for link in links:
        boundaries.update(
            (max(0, min(link.column, len(text))), max(0, min(link.end_column, len(text))))
        )

    ordered = sorted(boundaries)
    result = []

    for start, end in pairwise(ordered):
        part = escape(text[start:end])
        kind = next((kind for first, last, kind in spans if first <= start < last), None)

        if kind:
            part = f'<span class="tok-{kind}">{part}</span>'

        link = next((link for link in links if link.column <= start < link.end_column), None)

        if link:
            part = (
                f'<a class="symbol"'
                f' href="{_source_link(link.target.source_id, link.target.line)}" title="Go to'
                f' {escape(link.name, quote=True)}">{part}</a>'
            )

        result.append(part)

    return "".join(result) or " "


def _profile_headers(count_header: str | None = None, *, source: bool = True) -> list[str]:
    """Order shared profiling columns before table-specific measurements.

    Keep the same labels for line, function, file, and notebook costs. Omit
    counts and source text only when those fields do not apply to the table.

    Parameters
    ----------
    count_header : str | None, default=None
        Available count label, such as Samples or Calls.

    source : bool, default=True
        Whether the table includes source text or a function name.

    Returns
    -------
    list[str]
        Shared headings in time, location, count, and source order.

    """
    return [
        "Time",
        "Location",
        *([count_header] if count_header else []),
        *(["Source"] if source else []),
    ]


def _table(headers: list[str], rows: list[list[str]], *, css: str = "") -> str:
    """Render prepared HTML cells in a scrollable table.

    Escape column headings and display an empty-state message when no rows are
    available.

    Parameters
    ----------
    headers : list[str]
        Column labels escaped before rendering.

    rows : list[list[str]]
        Prepared row cells and any associated ranking measurements.

    css : str, default=''
        CSS classes applied to the rendered table.

    Returns
    -------
    str
        Table markup or an empty-state message.

    """
    if not rows:
        return '<p class="empty">No measurements available in this run.</p>'

    head = "".join(f'<th scope="col">{escape(header)}</th>' for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return (
        f'<div class="table-scroll"><table class="{css}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _sort_header(
    label: str,
    key: str,
    *,
    numeric: bool = True,
    active: bool = False,
    descending: bool = True,
    title: str = "",
    css: str = "",
) -> str:
    """Render a keyboard-accessible column sorter with a direction arrow.

    Keep the visible label separate from the arrow and expose the current
    direction through the column's `aria-sort` attribute.

    Parameters
    ----------
    label : str
        Visible column name, escaped before rendering. An empty name uses the
        tooltip or sort key as its accessible name.

    key : str
        Row data attribute containing the raw sortable value.

    numeric : bool, default=True
        Whether to compare raw numbers rather than alphabetical text.

    active : bool, default=False
        Whether this column describes the initially rendered row order.

    descending : bool, default=True
        Initial direction when this column is selected.

    title : str, default=''
        Measurement explanation retained on the heading.

    css : str, default=''
        Heading class used to size a narrow line-number column.

    Returns
    -------
    str
        Heading markup with an accessible button and sorting metadata.

    """
    direction = "descending" if descending else "ascending"
    next_direction = ("ascending" if descending else "descending") if active else direction
    state = f' aria-sort="{direction}"' if active else ""
    tooltip = f' title="{escape(title, quote=True)}"' if title else ""
    heading_class = f' class="{escape(css, quote=True)}"' if css else ""
    name = label or title or key
    heading_label = f' aria-label="{escape(name, quote=True)}"' if not label else ""
    return (
        f'<th scope="col"{heading_class}{state}{tooltip}{heading_label}>'
        '<button type="button" class="table-sort"'
        f' data-sort="{escape(key, quote=True)}"'
        f' data-sort-type="{"number" if numeric else "text"}"'
        f' data-sort-direction="{direction}"'
        f' data-sort-label="{escape(name, quote=True)}"'
        f' aria-label="Sort by {escape(name, quote=True)}, {next_direction}">'
        f'<span class="table-sort-label">{escape(label)}</span>'
        '<svg class="table-sort-icon" viewBox="0 0 12 12" fill="none"'
        ' stroke="currentColor" stroke-width="1.2" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true" focusable="false">'
        '<path class="sort-neutral" d="M3 10V2m-2 2 2-2 2 2M9 2v8m-2-2 2 2 2-2"></path>'
        '<path class="sort-ascending" d="M6 10V2M3 5l3-3 3 3"></path>'
        '<path class="sort-descending" d="M6 2v8M3 7l3 3 3-3"></path>'
        "</svg></button></th>"
    )


def _sort_values(values: dict[str, str | int | None]) -> str:
    """Encode raw row values without turning missing measurements into zero.

    Parameters
    ----------
    values : dict[str, str | int | None]
        Sort keys and values in original units; `None` becomes an empty value.

    Returns
    -------
    str
        Escaped HTML data attributes for numeric and alphabetical sorting.

    """
    return "".join(
        f' data-{escape(key, quote=True)}="'
        f'{escape(str(value), quote=True) if value is not None else ""}"'
        for key, value in values.items()
    )


def _heat_controls(*, memory: bool = False, enabled: bool = True) -> str:
    """Render the heatmap selector with the page's initial selection.

    Parameters
    ----------
    memory : bool, default=False
        Whether process-memory growth is available for these source lines.

    enabled : bool, default=True
        Whether to select Time initially instead of disabling row heat.

    Returns
    -------
    str
        Right-aligned heat selector with available measurement choices.

    """
    memory_heat = (
        '<button type="button" class="source-heat" data-heat="memory" aria-pressed="false"'
        ' title="Color by positive accumulated process memory change">Mem Growth</button>'
        if memory
        else ""
    )
    return (
        '<div class="source-controls"><div class="source-toolbar"><span>Heatmap by</span>'
        '<div class="source-heat-controls" role="group" aria-label="Color table rows">'
        '<button type="button" class="source-heat" data-heat="none"'
        f' aria-pressed="{str(not enabled).lower()}"'
        ' title="Disable heatmap">None</button>'
        '<button type="button" class="source-heat" data-heat="time"'
        f' aria-pressed="{str(enabled).lower()}"'
        f' title="Color by time">Time</button>{memory_heat}</div></div></div>'
    )


def _summary_table(
    headers: list[tuple[str, str]],
    rows: list[list[str]],
    values: list[dict[str, str | int | None]],
) -> str:
    """Render sortable summary rows with independently selectable time heat.

    Scale heat against the largest duration in this table. Preserve stable
    order for ties and keep unavailable times below measured zeroes. Leave
    rows uncolored until time heat is selected.

    Parameters
    ----------
    headers : list[tuple[str, str]]
        Visible column labels paired with their row data keys.

    rows : list[list[str]]
        Prepared HTML cells for each function or source snapshot.

    values : list[dict[str, str | int | None]]
        Raw measurements and names aligned with the prepared rows.

    Returns
    -------
    str
        Scrollable table or an empty-state message when no rows are available.

    """
    if not rows:
        return '<p class="empty">No measurements available in this run.</p>'

    head = "".join(
        _sort_header(
            label,
            key,
            numeric=key not in {"name", "kind"},
            active=key == "time",
            descending=key not in {"name", "kind", "line"},
        )
        for label, key in headers
    )
    maximum = max((int(value["time"] or 0) for value in values), default=0)
    ordered = sorted(
        zip(rows, values, strict=True),
        key=lambda item: (item[1]["time"] is None, -int(item[1]["time"] or 0)),
    )
    body = []
    for row, value in ordered:
        duration = int(value["time"]) if value["time"] is not None else None
        intensity = _heat(duration, maximum)
        body.append(
            '<tr class="heat-row" style="--heat:0.00000"'
            f' data-heat-time="{intensity:.5f}"'
            f"{_sort_values(value)}>{''.join(f'<td>{cell}</td>' for cell in row)}</tr>"
        )
    return (
        '<div class="table-scroll"><table class="sortable-table summary-table">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _metric_value(value: Any, unit: str = "") -> str:
    """Format a metric while respecting its reported measurement unit.

    Preserve unknown values and handle sequence-valued distributions
    consistently.

    Parameters
    ----------
    value : Any
        Measurement or serialized value to normalize or display.

    unit : str, default=''
        Captured source snapshot being inspected.

    Returns
    -------
    str
        Readable metric value with its reported units.

    """
    if value is None or (isinstance(value, float) and not isfinite(value)):
        return "—"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if unit == "ns":
            return _time(int(value))

        if unit == "bytes":
            return _bytes(int(value))

        return f"{value:,}"

    if isinstance(value, (list, tuple)):
        return ", ".join(_metric_value(item, unit) for item in value) or "—"

    return str(value)


def _metadata_rows(metadata: dict[str, Any], prefix: str = "", unit: str = "") -> list[list[str]]:
    """Flatten nested metadata into escaped label and value cells.

    Infer nanosecond and byte units from explicitly named metric suffixes.

    Parameters
    ----------
    metadata : dict[str, Any]
        Available context values or nested measurement metadata.

    prefix : str, default=''
        Prefix used to construct an identifier or nested display label.

    unit : str, default=''
        Captured source snapshot being inspected.

    Returns
    -------
    list[list[str]]
        Escaped detail labels and formatted value cells.

    """
    rows = []

    for key, value in metadata.items():
        key = str(key)
        value_unit = "ns" if key.endswith("_ns") else "bytes" if key.endswith("_bytes") else unit
        label = key.removesuffix("_ns").removesuffix("_bytes").replace("_", " ")
        label = f"{prefix} · {label}" if prefix else label[:1].upper() + label[1:]

        if isinstance(value, dict) and value:
            rows.extend(_metadata_rows(value, label, value_unit))
        else:
            rows.append([escape(label), escape(_metric_value(value, value_unit))])

    return rows


def _metadata(metadata: dict[str, Any]) -> str:
    """Render available context metadata as a detail table.

    Escape values through the shared metadata row formatter.

    Parameters
    ----------
    metadata : dict[str, Any]
        Available context values or nested measurement metadata.

    Returns
    -------
    str
        Rendered metadata detail table.

    """
    return _table(["Detail", "Value"], _metadata_rows(metadata))


def _operator_metrics(metrics: dict[str, Any]) -> str:
    """Render available operator metrics with their reported names and units.

    Keep unknown metrics unavailable and omit unsupported low-level counters.

    Parameters
    ----------
    metrics : dict[str, Any]
        Available operator metrics with retained measurement metadata.

    Returns
    -------
    str
        Rendered metric table or an unavailable message.

    """
    rows = []

    for key, metric in metrics.items():
        structured = isinstance(metric, dict)
        name = str(metric.get("name") or key) if structured else str(key).replace("_", " ")
        kind = str(metric.get("type", "")).lower() if structured else ""
        category = f"{key} {name}".lower()

        # Spark's merge/block/chunk counters obscure the useful SQL operator metrics.
        if any(word in category for word in ("block", "chunk", "merge")):
            continue

        if (
            structured
            and kind not in ("size", "timing", "nstiming")
            and not any(
                word in category
                for word in (
                    "row",
                    "byte",
                    "size",
                    "file",
                    "partition",
                    "shuffle",
                    "memory",
                    "spill",
                    "time",
                    "duration",
                )
            )
        ):
            continue

        value = metric.get("value") if structured else metric

        if kind in ("timing", "nstiming"):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value *= 1_000_000 if kind == "timing" else 1

            name = f"Reported operator time · {name}"
            formatted = _metric_value(value, "ns")
        elif kind == "size" or (
            not structured and ("byte" in category or str(key).endswith("_bytes"))
        ):
            formatted = _metric_value(value, "bytes")
        else:
            formatted = _metric_value(value)

        rows.append([escape(name), escape(formatted)])

    return (
        _table(["Operator metric", "Value"], rows)
        if rows
        else '<p class="muted">Operator metrics unavailable.</p>'
    )


def _spark_operators(operators: list[SparkOperator]) -> list[tuple[SparkOperator, _SparkCost]]:
    """Flatten operations while retaining measured fused-pipeline costs.

    Internal wrappers with no cost stay out of the ranking. Measured
    wrappers retain their own row; their time is never assigned to a child.

    """
    rows = []
    seen: set[str] = set()

    def visit(operator: SparkOperator) -> None:
        """Visit each operator once and retain meaningful ranking rows.

        Preserve measured wrapper costs without assigning them to logical
        children.

        Parameters
        ----------
        operator : [SparkOperator]
            Observed physical operator whose costs or children are inspected.

        """
        if operator.id in seen:
            return

        seen.add(operator.id)
        cost = _operator_cost(operator)
        wrapper = operator.name.startswith(
            ("WholeStageCodegen", "InputAdapter", "ResultQueryStage", "ShuffleQueryStage")
        )
        if not wrapper or any(
            value is not None for value in (cost.time_ns, cost.peak_memory_bytes, cost.spill_bytes)
        ):
            rows.append((operator, cost))

        for child in operator.children:
            visit(child)

    for operator in operators:
        visit(operator)

    return rows


def _spark_trigger(location: SourceLocation | None, sources: dict[str, SourceUnit]) -> str:
    """Render an action's exact trigger link and captured source text.

    Leave unavailable trigger snapshots explicitly unavailable.

    Parameters
    ----------
    location : [SourceLocation] | None
        Captured project source location, when available.

    sources : dict[str, SourceUnit]
        Captured source snapshots keyed by stable identifiers.

    Returns
    -------
    str
        Trigger link and escaped captured source, when available.

    """
    link = _spark_source_link(location, sources)
    unit = sources.get(location.source_id) if location else None
    if unit and location and 0 < location.line <= len(unit.source.splitlines()):
        text = unit.source.splitlines()[location.line - 1].strip()
        link += (
            f'<code class="spark-source" title="{escape(text, quote=True)}">{escape(text)}</code>'
        )

    return link


def _spark_cost_cells(cost: _SparkCost) -> list[str]:
    """Format comparable operator timing, peak memory, and spill cells.

    Explain the selected timing metric without implying cumulative action
    duration.

    Parameters
    ----------
    cost : _SparkCost
        Selected comparable operator cost values.

    Returns
    -------
    list[str]
        Formatted timing, peak memory, and spill cells.

    """
    label = f"Largest reported timing: {cost.time_label}" if cost.time_label else "Unavailable"
    return [
        (
            f'<strong class="hot-time" title="{escape(label, quote=True)}">'
            f"{_time(cost.time_ns)}</strong>"
        ),
        _bytes(cost.peak_memory_bytes),
        _bytes(cost.spill_bytes),
    ]


def _spark_cost_table(
    title: str,
    headers: list[str],
    rows: list[tuple[list[str], _SparkCost]],
    *,
    css: str,
) -> str:
    """Render a Spark cost ranking with independent ordering controls.

    Put known larger timings first while retaining missing metric values.

    Parameters
    ----------
    title : str
        Escaped title displayed above the ranking.

    headers : list[str]
        Column labels escaped before rendering.

    rows : list[tuple[list[str], _SparkCost]]
        Prepared row cells and any associated ranking measurements.

    css : str
        CSS classes applied to the rendered table.

    Returns
    -------
    str
        Ranking markup with independent ordering controls.

    """
    if not rows:
        return f'<h2>{escape(title)}</h2><p class="muted">Operator details unavailable.</p>'

    # Render the initial ranking in Python so the costs are readable without JS.
    rows = sorted(rows, key=lambda row: (row[1].time_ns is None, -(row[1].time_ns or 0)))
    controls = "".join(
        f'<button type="button" class="spark-order" data-order="{order}"'
        f' aria-pressed="{"true" if order == "time" else "false"}">{label}</button>'
        for order, label in (("time", "Time"), ("memory", "Memory"), ("spill", "Spill"))
    )
    head = "".join(f'<th scope="col">{escape(header)}</th>' for header in headers)
    body = []
    for cells, cost in rows:
        attributes = "".join(
            f' data-{name}="{"" if value is None else value}"'
            for name, value in (
                ("time", cost.time_ns),
                ("memory", cost.peak_memory_bytes),
                ("spill", cost.spill_bytes),
            )
        )
        body.append(f"<tr{attributes}>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")

    return (
        f'<div class="spark-cost-section"><div class="spark-cost-heading"><h2>{escape(title)}</h2>'
        f'<div class="spark-toolbar"><span>Highest first</span><div class="spark-order-controls"'
        f' role="group" aria-label="Order {escape(title.lower(), quote=True)}">{controls}</div>'
        f'</div></div><div class="table-scroll spark-cost-scroll"><table class="{css}">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div></div>"
    )


def _spark_operator_rows(
    executions: list[SparkExecution],
    sources: dict[str, SourceUnit],
    *,
    context: bool = False,
) -> list[tuple[list[str], _SparkCost]]:
    """Build ranked operator rows from observed action plans.

    Include action context when requested and preserve each metric's measurement
    domain.

    Parameters
    ----------
    executions : list[SparkExecution]
        Observed actions whose physical operators are rendered.

    sources : dict[str, SourceUnit]
        Captured source snapshots keyed by stable identifiers.

    context : bool, default=False
        Context or display option used by the operation.

    Returns
    -------
    list[tuple[list[str], _SparkCost]]
        Prepared operator cells paired with selected costs.

    """
    rows = []
    for execution in executions:
        for operator, cost in _spark_operators(execution.operators):
            name = (
                f"Fused pipeline · {operator.name}"
                if operator.name.startswith("WholeStageCodegen")
                else operator.name
            )
            cells = [
                (
                    f'<a class="spark-operator-link"'
                    f' href="#{_spark_operator_id(execution.id, operator.id)}"'
                    f' title="{escape(operator.description, quote=True)}">'
                    f"{escape(name)}"
                    f'<small class="spark-secondary">#{escape(operator.id)}</small></a>'
                )
            ]
            if context:
                cells.append(
                    f'<a href="#{_key("spark", execution.id)}">{escape(execution.name)} '
                    f'#{escape(execution.id[:8])}</a><small class="spark-secondary">'
                    f"Action trigger: "
                    f"{_spark_source_link(execution.location, sources)}</small>"
                )

            rows.append(([*cells, *_spark_cost_cells(cost)], cost))

    return rows


def _spark_step_label(step: _SparkStep) -> tuple[str, str]:
    """Explain a main operation and the work it performs.

    Parameters
    ----------
    step : _SparkStep
        Main physical operation with its captured description.

    Returns
    -------
    tuple[str, str]
        Plain-language title and brief explanation, without HTML markup.

    """
    explanations = {
        _StepKind.READ: "Load rows from a source or cached data.",
        _StepKind.GENERATE: "Create the input rows inside Spark.",
        _StepKind.FILTER: "Discard rows that do not match the condition.",
        _StepKind.JOIN: "Match rows from the separate inputs shown here.",
        _StepKind.AGGREGATE: "Combine rows into groups and calculate totals or other summaries.",
        _StepKind.SORT: "Put rows in order; large inputs can require memory or disk.",
        _StepKind.SHUFFLE: "Redistribute rows so related data reaches the same worker.",
        _StepKind.BROADCAST: "Send this input to workers so they can join it locally.",
        _StepKind.PYTHON: "Transfer data to Python workers and run Python code.",
        _StepKind.WINDOW: "Calculate values using a group or sequence of related rows.",
        _StepKind.UNION: "Append the rows from each input.",
        _StepKind.LIMIT: "Keep only the requested portion of the result.",
        _StepKind.WRITE: "Save the output to its destination.",
        _StepKind.PROJECT: "Select or calculate columns for each row.",
        _StepKind.OTHER: "Inspect the captured operator for this operation's details.",
    }
    title = str(step.kind)
    explanation = explanations[step.kind]
    if step.kind == _StepKind.OTHER:
        title = step.operator.name
    elif step.kind == _StepKind.AGGREGATE and "partial_" in step.operator.description.lower():
        title = "Summarize within each worker"
        explanation = "Reduce the input before combining summaries across workers."
    elif step.kind == _StepKind.LIMIT and "takeordered" in step.operator.name.lower():
        title = "Sort and take a limited result"
    return title, explanation


def _spark_group_label(pipeline: _SparkPipeline) -> str:
    """Describe which main steps share a fused measurement.

    Parameters
    ----------
    pipeline : _SparkPipeline
        Measured fused group and the main steps inside its boundary.

    Returns
    -------
    str
        Step references or an honest label for unavailable group members.

    """
    numbers = ", ".join(str(number) for number in pipeline.steps)
    if len(pipeline.steps) == 1:
        return f"Step {numbers} pipeline"
    return f"Steps {numbers} together" if numbers else "Combined pipeline"


def _spark_findings(steps: list[_SparkStep], pipelines: list[_SparkPipeline]) -> str:
    """Point to measured costs and row multiplication.

    Compare independent reported counters rather than adding overlapping
    costs or calculating percentages of action wall time.

    Parameters
    ----------
    steps : list[_SparkStep]
        Main operations in dependency order.

    pipelines : list[_SparkPipeline]
        Shared measurements whose work cannot be split into step timings.

    Returns
    -------
    str
        Compact investigation cues above the main-step table.

    """
    costs = [(f"Step {step.number} · {_spark_step_label(step)[0]}", step.cost) for step in steps]
    costs.extend((_spark_group_label(pipeline), pipeline.cost) for pipeline in pipelines)
    cards = []
    for title, attribute, formatter, missing in (
        ("Largest reported time", "time_ns", _time, "Separate step timings unavailable."),
        (
            "Largest reported memory",
            "peak_memory_bytes",
            _bytes,
            "Peak-memory counters unavailable.",
        ),
        ("Largest disk spill", "spill_bytes", _bytes, "Disk-spill counters unavailable."),
    ):
        known = [(label, getattr(cost, attribute)) for label, cost in costs]
        known = [(label, value) for label, value in known if value is not None]
        label, value = max(known, key=lambda item: item[1]) if known else (missing, None)
        if attribute == "spill_bytes" and value == 0:
            label = "Measured counters report no disk spill."
        cards.append((title, formatter(value), label))

    growth = []
    for step in steps:
        if step.kind != _StepKind.JOIN or step.rows is None or len(step.inputs) < 2:
            continue

        counts = [steps[number - 1].rows for number in step.inputs]
        if all(count is not None and count > 0 for count in counts):
            largest = max(count for count in counts if count is not None)
            if step.rows > largest:
                growth.append((step.rows / largest, step.number))

    if growth:
        factor, number = max(growth)
        cards.append(
            (
                "Rows multiplied at a join",
                f"{factor:,.1f}x",
                f"Step {number} · versus its largest input",
            )
        )

    return (
        '<div class="spark-findings">'
        + "".join(
            f'<div class="spark-finding"><span>{escape(title)}</span>'
            f"<strong>{escape(value)}</strong>"
            f"<small>{escape(label)}</small></div>"
            for title, value, label in cards
        )
        + "</div>"
    )


def _spark_plan_overview(execution: SparkExecution) -> str:
    """Show main data-flow steps, row counts, and separately scoped costs.

    Keep parallel input branches explicit and hide implementation plumbing.
    Missing row counts remain unknown unless an operation preserves a known
    input count. Shared pipeline costs are never assigned to children.

    Parameters
    ----------
    execution : [SparkExecution]
        Captured action whose physical plan is summarized.

    Returns
    -------
    str
        Main-step overview with links to original physical-plan details.

    """
    steps, pipelines = _spark_steps(execution.operators)
    if not steps and not pipelines:
        return '<p class="empty">Main plan steps unavailable in this environment.</p>'

    maximum = max((step.cost.time_ns or 0 for step in steps), default=0)
    rows = []
    for step in steps:
        title, explanation = _spark_step_label(step)
        group = next((pipeline for pipeline in pipelines if step.number in pipeline.steps), None)
        timing = _spark_cost_cells(step.cost)[0]
        if step.cost.time_ns is None and group is not None and group.cost.time_ns is not None:
            group_label = escape(_spark_group_label(group), quote=True)
            timing = f'<span class="spark-shared" title="{group_label}">Shared timing</span>'
        elif maximum and step.cost.time_ns is not None:
            timing += (
                f'<span class="spark-cost-bar" aria-hidden="true"><span'
                f' style="width:{100 * step.cost.time_ns / maximum:.1f}%"></span></span>'
            )

        count = f"<strong>{_count(step.rows)}</strong>"
        if step.cost.time_ns is not None and step.cost.time_label:
            timing += f'<small class="spark-secondary">{escape(step.cost.time_label)}</small>'
        if step.rows is not None:
            count += (
                '<small class="spark-secondary">From input · unchanged</small>'
                if step.rows_from_input
                else '<small class="spark-secondary">Measured output</small>'
            )
        hints = []
        if step.cost.spill_bytes:
            hints.append(f"{_bytes(step.cost.spill_bytes)} spilled to disk")
        if step.rows is not None and len(step.inputs) == 1 and not step.rows_from_input:
            previous = steps[step.inputs[0] - 1].rows
            if previous and previous != step.rows:
                fraction = step.rows / previous
                percentage = "<0.1%" if 0 < fraction < 0.001 else f"{fraction:,.1%}"
                hints.append(f"{percentage} of input row count")

        hint = (
            f'<small class="spark-step-hint">{escape(" · ".join(hints))}</small>' if hints else ""
        )
        inputs = " + ".join(f'<span class="spark-input">{number}</span>' for number in step.inputs)
        cells = [
            (
                f'<div class="spark-step-title"><span class="spark-step-number">{step.number}'
                f'</span><a class="spark-step-link"'
                f' href="#{_spark_operator_id(execution.id, step.operator.id)}"'
                f' title="{escape(step.operator.description or step.operator.name, quote=True)}">'
                f"{escape(title)}</a></div>"
                f'<small class="spark-step-description">{escape(explanation)}</small>{hint}'
            ),
            inputs or '<span class="muted">Source</span>',
            timing,
            _bytes(step.cost.peak_memory_bytes),
            count,
        ]
        rows.append(cells)

    shared = ""
    if pipelines:
        shared_rows = [
            [
                (
                    f'<a href="#{_spark_operator_id(execution.id, pipeline.operator.id)}">'
                    f"{escape(_spark_group_label(pipeline))}</a>"
                ),
                *_spark_cost_cells(pipeline.cost),
            ]
            for pipeline in pipelines
        ]
        shared = (
            '<div class="spark-shared-costs"><h3>Operations measured together</h3>'
            '<p class="muted">Spark runs these steps as one combined pipeline. Its cost cannot'
            " be split reliably between the individual steps.</p>"
            + _table(["Shared steps", "Reported time", "Peak memory", "Spill"], shared_rows)
            + "</div>"
        )

    provenance = ""
    if str(execution.metadata.get("plan_origin", "")).startswith("input DataFrame"):
        provenance = (
            '<p class="spark-limitation">Input DataFrame plan only. This may differ from the'
            " plan used by the action.</p>"
        )
    elif execution.metadata.get("aqe_final_plan") is False:
        provenance = '<p class="spark-limitation">The final adaptive plan was unavailable.</p>'

    step_table = _table(
        ["Step", "From step", "Reported time", "Peak memory", "Rows after"],
        rows,
        css="spark-steps",
    )
    return (
        '<div class="spark-plan-overview"><div class="section-heading"><h2>Main plan steps</h2>'
        f'<span class="muted">{len(steps)} steps · inputs → result</span></div>{provenance}'
        f"{_spark_findings(steps, pipelines)}"
        f"{step_table}"
        f"{shared}"
        '<p class="spark-metric-note">Read in data-flow order; separate inputs can run in'
        " parallel. Reported times can include upstream work or waiting, overlap, and do not"
        " add up to wall time. Memory is the"
        " reported peak counter, not total data size. A dash means unavailable;"
        " “From input” carries a known count through a step that preserves rows.</p></div>"
    )


def _operator(operator: SparkOperator, execution_id: str) -> str:
    """Render an escaped physical operator tree and its available metrics.

    Flatten unmeasured internal wrappers while preserving measured pipeline
    nodes.

    Parameters
    ----------
    operator : [SparkOperator]
        Observed physical operator whose costs or children are inspected.

    execution_id : str
        Owning action identity used to scope operator navigation targets.

    Returns
    -------
    str
        Escaped operator markup.

    """
    children = "".join(_operator(child, execution_id) for child in operator.children)

    if (
        operator.name.startswith(
            ("WholeStageCodegen", "InputAdapter", "ResultQueryStage", "ShuffleQueryStage")
        )
        and _operator_cost(operator) == _SparkCost()
    ):
        return children

    metrics = _operator_metrics(operator.metrics)
    return (
        f'<li><details id="{_spark_operator_id(execution_id, operator.id)}"'
        f' class="spark-operator"><summary><span class="operator-dot"></span>'
        f"{escape(operator.name)} "
        f'<small>#{escape(operator.id)}</small></summary><div class="operator-detail">'
        f"<pre>{escape(operator.description)}</pre>{metrics}</div>"
        f"</details>{'<ul>' + children + '</ul>' if children else ''}</li>"
    )


def _heat(value: int | None, maximum: int) -> float:
    """Scale positive measurements so smaller hotspots remain visible.

    Leave unknown, zero, and negative measurements uncolored. Use a shared
    maximum to keep heat comparable across source files.

    Parameters
    ----------
    value : int | None
        Line duration in nanoseconds or process-memory growth in bytes.

    maximum : int
        Largest positive measurement for the selected metric in the report.

    Returns
    -------
    float
        Logarithmic heat intensity between zero and one.

    """
    return log1p(999 * max(value or 0, 0) / maximum) / log1p(999) if maximum else 0


def _notebook_path(unit: SourceUnit) -> str:
    """Identify the notebook shared by its captured cell snapshots.

    Prefer the canonical notebook URI so display labels cannot merge different
    notebooks. Preserve path characters before the final cell fragment.

    Parameters
    ----------
    unit : [SourceUnit]
        Notebook snapshot whose identity or cell label supplies its path.

    Returns
    -------
    str
        Notebook path without a cell or snapshot suffix.

    """
    if unit.id.startswith("notebook://"):
        identity = unit.id.removeprefix("notebook://")
        path, separator, fragment = identity.rpartition("#")
        return path if separator and fragment.startswith(("cell-", "snapshot-")) else identity

    return unit.path.rsplit(" · cell ", 1)[0]


def _source_name(unit: SourceUnit, *, include_cell: bool = True) -> str:
    """Display a source filename while retaining its canonical identity.

    Strip directories using either platform's path separator. Notebook names
    come from their canonical paths, so identically named notebooks remain
    separate source groups and keep their original navigation targets.

    Parameters
    ----------
    unit : [SourceUnit]
        Captured source whose filename is displayed.

    include_cell : bool, default=True
        Whether to retain the captured notebook cell label for line links.

    Returns
    -------
    str
        Filename with its extension and optional notebook cell label.

    """
    notebook = unit.kind == SourceKind.NOTEBOOK
    path = _notebook_path(unit) if notebook else unit.path
    name = PurePath(path.replace("\\", "/")).name
    if notebook and include_cell:
        _, separator, cell = unit.path.rpartition(" · cell ")
        if separator:
            name += f" · cell {cell}"
    return name


def _source_groups(sources: list[SourceUnit]) -> list[list[SourceUnit]]:
    """Group notebook cells while retaining independent Python snapshots.

    Keep notebooks and their cells in capture order. Group only presentation;
    measurements and navigation continue to use each snapshot's identity.

    Parameters
    ----------
    sources : list[[SourceUnit]]
        Display snapshots in their original capture order.

    Returns
    -------
    list[list[[SourceUnit]]]
        One group per notebook or individual Python source snapshot.

    """
    groups: dict[tuple[SourceKind, str], list[SourceUnit]] = {}
    for unit in sources:
        identity = _notebook_path(unit) if unit.kind == SourceKind.NOTEBOOK else unit.id
        groups.setdefault((SourceKind(unit.kind), identity), []).append(unit)
    return list(groups.values())


def _cell_label(unit: SourceUnit, position: int) -> str:
    """Label a captured cell without exposing its source digest.

    Retain frontend cell IDs or execution counts when supplied. Use the capture
    position for snapshots without a cell identity.

    Parameters
    ----------
    unit : [SourceUnit]
        Notebook snapshot whose cell identity is displayed.

    position : int
        One-based position within the notebook's captured snapshots.

    Returns
    -------
    str
        Cell label or a label for an unsplit notebook snapshot.

    """
    _, separator, label = unit.path.rpartition(" · cell ")
    if separator:
        return f"Cell {label}"

    fragment = unit.id.rpartition("#")[2]
    if fragment.startswith("snapshot-"):
        return f"Snapshot {position}"
    if fragment.startswith("cell-"):
        identity = fragment.removeprefix("cell-")
        cell, separator, digest = identity.rpartition("-")
        if separator and len(digest) == 12 and all(char in "0123456789abcdef" for char in digest):
            identity = cell
        return f"Cell {identity}"
    return f"Cell {position}"


def _source_page(
    units: list[SourceUnit],
    data: dict[tuple[str, int], LineStats],
    maximum: int,
    maximum_memory: int,
    capabilities: dict[str, BackendCapabilities],
    default_capabilities: BackendCapabilities,
    total_time: int | None,
) -> str:
    """Render complete captured source with line metrics and navigation.

    Show total measured line time beside the source count. Keep unknown
    measurements, heat scaling, and optional memory or GPU columns consistent.

    Parameters
    ----------
    units : list[[SourceUnit]]
        Captured notebook cells or one Python source snapshot.

    data : dict[tuple[str, int], LineStats]
        Normalized or serialized values used by this operation.

    maximum : int
        Largest visible line duration used to scale source heat.

    maximum_memory : int
        Largest visible positive process-memory change, in bytes, used to
        scale memory-growth heat.

    capabilities : dict[str, [BackendCapabilities]]
        Collector capabilities indexed by individual snapshot identity.

    default_capabilities : [BackendCapabilities]
        Collector capabilities for snapshots without their own entry.

    total_time : int | None
        Sum of available line times in nanoseconds across this source group,
        matching the Files summary; None when no line time is available.

    Returns
    -------
    str
        Complete source page markup with available measurements.

    """
    page = _key("source", units[0].id)
    notebook = units[0].kind == SourceKind.NOTEBOOK
    title = _source_name(units[0], include_cell=False)
    spans = {unit.id: _tokens(unit.source) for unit in units}
    collectors = {unit.id: capabilities.get(unit.id, default_capabilities) for unit in units}
    memory = any(item.memory for item in collectors.values())
    gpu = any(item.gpu for item in collectors.values())
    source_lines = [
        (unit, number, text, data.get((unit.id, number)))
        for unit in units
        for number, text in enumerate(unit.source.splitlines() or [""], 1)
    ]
    source_stats = [stats for _, _, _, stats in source_lines]
    # Preserve known counts on sources shared by collectors with different capabilities.
    hit_counts = any(item.hit_counts for item in collectors.values()) or any(
        stats is not None and stats.hits is not None for stats in source_stats
    )
    sample_counts = any(item.sample_counts for item in collectors.values()) or any(
        stats is not None and stats.samples is not None for stats in source_stats
    )
    context = any(
        stats is not None and (stats.spark_executions or stats.notebook_runs)
        for stats in source_stats
    )
    rows: dict[str, list[str]] = {unit.id: [] for unit in units}

    for unit, number, text, stats in source_lines:
        collector = collectors[unit.id]
        anchor = _key("source", unit.id)
        duration = stats.wall_time_ns if stats else None
        ram = stats.ram if collector.memory and stats else None
        delta = ram.delta_bytes if ram else None
        hits = stats.hits if stats else (0 if collector.hit_counts else None)

        if hits is None and collector.hit_counts and duration is None:
            hits = 0

        average = None if duration is None or not hits else duration // hits
        samples = stats.samples if stats else None
        if samples is None and collector.sample_counts and (stats is None or duration is None):
            samples = 0
        hit_cells = (
            f'<td class="metric">{_count(hits)}</td><td class="metric">{_time(average)}</td>'
            if hit_counts
            else ""
        )
        sample_cell = f'<td class="metric">{_count(samples)}</td>' if sample_counts else ""
        intensity = _heat(duration, maximum)
        memory_intensity = _heat(delta, maximum_memory)
        refs = []

        if stats:
            refs.extend(
                (
                    f'<a class="badge spark" href="#{_key("spark", value)}" title="Open Spark'
                    f' execution">Spark #{escape(value[:8])}</a>'
                )
                for value in stats.spark_executions
            )
            refs.extend(
                (
                    f'<a class="badge" href="#{_key("run", value)}" title="Open notebook'
                    f' invocation">Notebook</a>'
                )
                for value in stats.notebook_runs
            )

        context_cell = f'<td class="references">{"".join(refs)}</td>' if context else ""
        memory_cells = ""
        allocation = None
        peak = None

        if memory:
            peak = ram.peak_bytes if ram else None
            allocation = stats.memory.delta_bytes if stats and stats.memory else None
            memory_cells = (
                f'<td class="metric">{_bytes(delta, signed=True)}</td>'
                f'<td class="metric">{_bytes(peak)}</td>'
            )
        gpu_cells = ""
        gpu_time = stats.gpu.time_ns if collector.gpu and stats and stats.gpu else None
        gpu_memory = stats.gpu.peak_memory_bytes if collector.gpu and stats and stats.gpu else None
        if gpu:
            gpu_cells = (
                f'<td class="metric">{_time(gpu_time)}</td>'
                f'<td class="metric">{_bytes(gpu_memory)}</td>'
            )

        values: dict[str, str | int | None] = {
            "line": number,
            "time": duration,
            "hits": hits,
            "average": average,
            "samples": samples,
            "memory": delta,
            "peak": peak,
            "gpu-time": gpu_time,
            "gpu-memory": gpu_memory,
            "allocation": allocation,
            "context": (
                " ".join(
                    [f"Spark #{value[:8]}" for value in stats.spark_executions]
                    + ["Notebook" for _ in stats.notebook_runs]
                )
                if stats
                else ""
            ),
        }
        rows[unit.id].append(
            f'<tr id="{anchor}-L{number}" class="source-row heat-row"'
            f' style="--heat:{intensity:.5f}"'
            f' data-heat-time="{intensity:.5f}" data-heat-memory="{memory_intensity:.5f}"'
            f"{_sort_values(values)}>"
            f'<td class="line-number"><a href="#{anchor}-L{number}" aria-label="Line'
            f' {number}">{number}</a></td>'
            f'<td class="metric">{_time(duration)}</td>{sample_cell}'
            f"{hit_cells}{memory_cells}{gpu_cells}{context_cell}<td"
            f' class="source-code"><code>{_code(text, spans[unit.id].get(number, []), stats)}'
            f"</code></td></tr>"
        )

    memory_head = (
        _sort_header(
            "Mem Change", "memory", title="Process memory changes across executions of this line"
        )
        + _sort_header(
            "Peak Mem", "peak", title="Highest process memory observed during this line"
        )
        if memory
        else ""
    )
    gpu_head = (
        _sort_header("Estimated GPU time", "gpu-time")
        + _sort_header("GPU peak memory", "gpu-memory")
        if gpu
        else ""
    )
    hit_head = (
        _sort_header("Hits", "hits") + _sort_header("Avg / hit", "average") if hit_counts else ""
    )
    sample_head = _sort_header("Samples", "samples") if sample_counts else ""
    context_head = (
        _sort_header("Context", "context", numeric=False, descending=False) if context else ""
    )
    timing = (
        "Sampled estimates of Python line time"
        if any(item.sampled for item in collectors.values())
        else "Python line time"
    )
    line_head = '<th scope="col" class="line-number" aria-label="Line number"></th>'
    source_head = _sort_header(
        "Source", "line", active=True, descending=False, title="Sort by line number"
    )
    time_head = _sort_header("Time", "time", title=timing)
    bodies = []
    labels = [_cell_label(unit, index) for index, unit in enumerate(units, 1)] if notebook else []
    label_counts = Counter(labels)
    revisions: dict[str, int] = {}
    columns = 3 + sample_counts + 2 * hit_counts + 2 * memory + 2 * gpu + context
    for index, unit in enumerate(units):
        heading = ""
        if notebook:
            label = labels[index]
            revisions[label] = revisions.get(label, 0) + 1
            if label_counts[label] > 1:
                label += f" · snapshot {revisions[label]}"
            heading = (
                f'<tr class="source-cell-heading"><th scope="rowgroup" colspan="{columns}">'
                f"{escape(label)}</th></tr>"
            )
        bodies.append(f"<tbody>{heading}{''.join(rows[unit.id])}</tbody>")
    count = f"{len(source_lines)} lines"
    if notebook:
        count = f"{len(units)} cells · {count}"
    return (
        f'<section id="{page}" class="page source-page" hidden><div class="source-header">'
        f'<h1 id="{page}-title" class="path-title">{escape(title)}</h1>'
        f'<div class="source-summary"><p class="muted">{count} · '
        f'<span title="{timing}">Total time: {_time(total_time)}</span></p>'
        f"{_heat_controls(memory=memory)}</div></div>"
        f'<div class="source-scroll" tabindex="0" role="region"'
        f' aria-labelledby="{page}-title"><table'
        f' class="source-table sortable-table"><thead><tr>{line_head}{time_head}{sample_head}'
        f"{hit_head}{memory_head}{gpu_head}{context_head}"
        f"{source_head}</tr></thead>"
        f"{''.join(bodies)}</table></div></section>"
    )


def _combined_lines(runs: list[ProfileRun]) -> dict[tuple[str, int], LineStats]:
    """Combine repeated source locations across runs without inventing counts.

    Keep hit and sample totals unknown if a contributing run lacks counts.

    """
    output: dict[tuple[str, int], LineStats] = {}

    for run in runs:
        for line in run.lines:
            key = (line.location.source_id, line.location.line)

            if key not in output:
                output[key] = deepcopy(line)
                continue

            merged = output[key]
            merged.wall_time_ns = _total([merged.wall_time_ns, line.wall_time_ns])
            # A sampled child cannot establish a complete execution count,
            # even when the other contributing runs counted their hits.
            merged.hits = (
                merged.hits + line.hits
                if merged.hits is not None and line.hits is not None
                else None
            )
            merged.samples = (
                merged.samples + line.samples
                if merged.samples is not None and line.samples is not None
                else None
            )

            if merged.ram is None:
                merged.ram = deepcopy(line.ram)
            elif line.ram is not None:
                # Different runs may belong to different processes. Absolute
                # RAM and changes cannot be summed into one execution step.
                merged.ram.rss_bytes = None
                merged.ram.delta_bytes = None
                peaks = [
                    value
                    for value in (merged.ram.peak_bytes, line.ram.peak_bytes)
                    if value is not None
                ]
                merged.ram.peak_bytes = max(peaks) if peaks else None

            if merged.memory is None:
                merged.memory = deepcopy(line.memory)
            elif line.memory is not None:
                merged.memory.delta_bytes = _total(
                    [merged.memory.delta_bytes, line.memory.delta_bytes]
                )
                peaks = [
                    value
                    for value in (merged.memory.peak_bytes, line.memory.peak_bytes)
                    if value is not None
                ]
                merged.memory.peak_bytes = max(peaks) if peaks else None
            if merged.gpu is None:
                merged.gpu = deepcopy(line.gpu)
            elif line.gpu is not None:
                merged.gpu.time_ns = _total([merged.gpu.time_ns, line.gpu.time_ns])
                gpu_peaks = [
                    value
                    for value in (merged.gpu.peak_memory_bytes, line.gpu.peak_memory_bytes)
                    if value is not None
                ]
                merged.gpu.peak_memory_bytes = max(gpu_peaks) if gpu_peaks else None

            for name in ("calls", "spark_executions", "notebook_runs"):
                existing = getattr(merged, name)
                existing.extend(value for value in getattr(line, name) if value not in existing)

    return output


def _source_capabilities(
    result: ProfileResult,
    runs: list[ProfileRun],
) -> dict[str, BackendCapabilities]:
    """Keep merged child sampling and memory semantics visible per source page.

    Use each child's recorded capabilities when sources span several
    backends.

    """
    collected: dict[str, list[BackendCapabilities]] = {}
    inherited = {id(result.root_run): result.capabilities}

    for run in runs:
        values = run.metadata.get("child_capabilities")
        capability = (
            BackendCapabilities(**values)
            if isinstance(values, dict)
            else inherited.get(id(run), result.capabilities)
        )

        for child in run.children:
            inherited[id(child)] = capability

        ids = {line.location.source_id for line in run.lines}
        ids.update(run.metadata.get("child_source_ids", []))

        if run.source:
            ids.add(run.source.id)

        for source_id in ids:
            collected.setdefault(source_id, []).append(capability)

    return {
        source_id: BackendCapabilities(
            sampled=any(item.sampled for item in values),
            memory=any(item.memory for item in values),
            gpu=any(item.gpu for item in values),
            hit_counts=all(item.hit_counts for item in values),
            sample_counts=all(item.sample_counts for item in values),
        )
        for source_id, values in collected.items()
    }


def _notebook_icon() -> str:
    """Render a notebook outline for decorative source navigation.

    Returns
    -------
    str
        Embedded SVG inheriting the surrounding navigation text color.

    """
    return (
        '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="5" y="3" width="15" height="18" rx="2"></rect>'
        '<path d="M9 3v18M3 7h4m-4 5h4m-4 5h4M12 8h5m-5 4h5'
        'm-5 4h3"></path></svg>'
    )


def _memory_location(location: SourceLocation | None, sources: dict[str, SourceUnit]) -> str:
    """Link a RAM observation to valid snapshotted source.

    Parameters
    ----------
    location : [SourceLocation] | None
        Observed project line, or None for an unlinked reading.

    sources : dict[str, [SourceUnit]]
        Snapshots available for source navigation.

    Returns
    -------
    str
        Escaped source link or an unlinked observation label.

    """
    unit = sources.get(location.source_id) if location is not None else None
    if unit is None or location is None or not 0 < location.line <= len(unit.source.splitlines()):
        return "No linked project line"
    name = _source_name(unit)
    return f'<a href="{_source_link(unit.id, location.line)}">{escape(name)}:{location.line}</a>'


def _memory_source(location: SourceLocation, sources: dict[str, SourceUnit]) -> str:
    """Render the captured code line for a memory-growth measurement.

    Escape snapshot text and keep missing or invalid locations unavailable.

    Parameters
    ----------
    location : [SourceLocation]
        One-based source line associated with the measured change.

    sources : dict[str, [SourceUnit]]
        Snapshots available for displaying the captured source text.

    Returns
    -------
    str
        Escaped code markup, or an em dash when no snapshot line is available.

    """
    unit = sources.get(location.source_id)
    if unit is None or not 0 < location.line <= len(unit.source.splitlines()):
        return "—"
    text = unit.source.splitlines()[location.line - 1].strip()
    return f"<code>{escape(text)}</code>"


def _memory_badges(run: ProfileRun, sources: dict[str, SourceUnit]) -> str:
    """Summarize observed RAM peaks and accumulated line changes for one run.

    Keep missing readings distinct from zero and use each run's own samples
    and line measurements so separate processes retain independent summaries.

    Parameters
    ----------
    run : [ProfileRun]
        Run owning the process RAM readings and accumulated line changes.

    sources : dict[str, [SourceUnit]]
        Snapshots available for linking the largest line change.

    Returns
    -------
    str
        Peak-memory value and largest line change with its source link.

    """
    peak = max(
        (sample for sample in run.memory_samples if sample.rss_bytes is not None),
        key=lambda sample: sample.rss_bytes or 0,
        default=None,
    )
    change = max(
        (line for line in run.lines if line.ram is not None and line.ram.delta_bytes is not None),
        key=lambda line: (line.ram.delta_bytes or 0) if line.ram is not None else 0,
        default=None,
    )
    change_detail = (
        _memory_location(change.location, sources)
        if change is not None
        else "No available line changes"
    )
    peak_value = _bytes(peak.rss_bytes if peak is not None else None)
    change_value = _bytes(
        change.ram.delta_bytes if change is not None and change.ram is not None else None,
        signed=True,
    )
    return (
        '<dl class="stats memory-stats"><div class="stat">'
        '<dt title="Highest observed physical RAM used by the whole Python process">'
        "Peak memory</dt>"
        f"<dd><strong>{peak_value}</strong></dd></div>"
        '<div class="stat"><dt title="Highest accumulated process-memory change on a single '
        'source line; repeated executions are summed">Largest line change</dt>'
        f"<dd><strong>{change_value}</strong><small>{change_detail}</small></dd></div></dl>"
    )


def _memory_timeline(run: ProfileRun, sources: dict[str, SourceUnit]) -> str:
    """Render a run's RAM timeline without combining separate processes.

    Parameters
    ----------
    run : [ProfileRun]
        Run containing chronological process RAM observations.

    sources : dict[str, [SourceUnit]]
        Snapshots available for point and hover navigation.

    Returns
    -------
    str
        Interactive SVG chart with exact readings and source links on hover.

    """
    samples = run.memory_samples
    known = [item.rss_bytes for item in samples if item.rss_bytes is not None]
    if not known:
        return '<p class="empty">Process RAM readings unavailable for this run.</p>'
    peak = max(known)
    scale = max(1, peak)
    duration = max(1, samples[-1].elapsed_ns)
    peak_index = next(index for index, item in enumerate(samples) if item.rss_bytes == peak)
    points = []
    segments: list[str] = []
    segment: list[str] = []
    for index, sample in enumerate(samples):
        location = _memory_location(sample.location, sources)
        x = 125 + 835 * sample.elapsed_ns / duration
        seconds, nanoseconds = divmod(sample.elapsed_ns, 1_000_000_000)
        elapsed = f"{seconds}.{nanoseconds:09d}".rstrip("0").rstrip(".") + " s"
        ram = (
            f"{_bytes(sample.rss_bytes)} ({sample.rss_bytes:,} bytes)"
            if sample.rss_bytes is not None
            else "Unavailable"
        )
        label = f"Time: {elapsed} · RAM: {ram}"
        point = ""
        if sample.rss_bytes is None:
            if segment:
                segments.append(" ".join(segment))
                segment = []
        else:
            y = 260 - 225 * sample.rss_bytes / scale
            segment.append(f"{x:.2f},{y:.2f}")
            point = (
                f'<circle class="memory-point" cx="{x:.2f}" cy="{y:.2f}" r="3">'
                f"<title>{escape(label)}</title></circle>"
            )
        if location.startswith("<a") and sample.location is not None:
            unit = sources[sample.location.source_id]
            name = _source_name(unit)
            source_label = f"{name}:{sample.location.line}"
            point = (
                f'<a href="{_source_link(sample.location.source_id, sample.location.line)}"'
                f' data-source-label="{escape(source_label, quote=True)}" tabindex="-1"'
                f' aria-label="{escape(source_label + " · " + label, quote=True)}">{point}</a>'
            )
        points.append(
            f'<g class="memory-observation" data-index="{index}" data-x="{x:.6f}"'
            f' data-time="{elapsed}" data-ram="{ram}"'
            f' data-label="{escape(label, quote=True)}">{point}</g>'
        )
    if segment:
        segments.append(" ".join(segment))
    gradient_id = _key("memory-fill", run.id)
    areas = "".join(
        f'<polygon points="{segment.split()[0].split(",")[0]},260 {segment} '
        f'{segment.split()[-1].split(",")[0]},260" fill="url(#{gradient_id})"/>'
        for segment in segments
    )
    plot = "".join(f'<polyline points="{segment}"/>' for segment in segments)
    ticks = "".join(
        f'<text x="110" y="{y + 4}" text-anchor="end">{_bytes(round(scale * fraction))}</text>'
        f'<line x1="125" y1="{y}" x2="960" y2="{y}"/>'
        for fraction in (1, 0.75, 0.5, 0.25, 0)
        for y in (260 - 225 * fraction,)
    )
    ticks += "".join(
        f'<text x="{x}" y="285" text-anchor="{anchor}">{_time(value)}</text>'
        for x, value, anchor in (
            (125, 0, "start"),
            (542.5, duration // 2, "middle"),
            (960, duration, "end"),
        )
    )
    tooltip_id = _key("memory-tooltip", run.id)
    return (
        '<div class="memory-inspector"><div class="memory-chart-heading">'
        "<h2>Memory Usage</h2></div>"
        '<div class="memory-chart-scroll">'
        f'<svg class="memory-chart" viewBox="0 0 1000 320" role="group" tabindex="0"'
        f' data-initial-index="{peak_index}" aria-describedby="{tooltip_id}"'
        f' aria-label="RAM over elapsed time; observed peak {_bytes(peak)}. '
        'Use the arrow keys to inspect readings and Enter to open linked source.">'
        f'<defs><linearGradient id="{gradient_id}" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#22d3ee" stop-opacity=".28"/>'
        '<stop offset="100%" stop-color="#22d3ee" stop-opacity=".02"/>'
        f'</linearGradient></defs><g class="memory-axis">{ticks}'
        '<text class="memory-axis-title" x="960" y="311" text-anchor="end">'
        "Elapsed time</text></g>"
        f'<g class="memory-area">{areas}</g><g class="memory-plot">{plot}</g>'
        '<line class="memory-guide" x1="125" x2="125" y1="35" y2="260"/>'
        f"{''.join(points)}</svg>"
        f'<div id="{tooltip_id}" class="memory-tooltip" role="status" hidden></div>'
        "</div></div>"
    )


def _memory_page(runs: list[ProfileRun], sources: dict[str, SourceUnit]) -> str:
    """Render process RAM growth and a separate timeline for each run.

    Parameters
    ----------
    runs : list[ProfileRun]
        Profiling scopes, including separately executed child notebooks.

    sources : dict[str, [SourceUnit]]
        Snapshot mapping used for source links.

    Returns
    -------
    str
        Memory report page with source-linked growth rankings and timelines.

    """
    sections = []
    measured_runs = [run for run in runs if run.memory_samples]
    for run in measured_runs:
        growth = sorted(
            (
                line
                for line in run.lines
                if line.ram is not None and line.ram.delta_bytes is not None
            ),
            key=lambda line: (line.ram.delta_bytes or 0) if line.ram is not None else 0,
            reverse=True,
        )
        rows = [
            [
                _bytes(line.ram.delta_bytes, signed=True),
                _bytes(line.ram.peak_bytes),
                _memory_location(line.location, sources),
                _memory_source(line.location, sources),
            ]
            for line in growth[:10]
            if line.ram is not None and line.ram.delta_bytes is not None
        ]
        heading = ""
        if len(measured_runs) > 1 or run is not runs[0]:
            heading = f"<h2>{'Main run' if run is runs[0] else escape(run.name)}</h2>"
        table = _table(["Mem Change", "Peak Mem", "Location", "Source"], rows, css="memory-growth")
        sections.append(
            f'<section class="memory-run" id="{_key("memory-run", run.id)}">{heading}'
            f"{_memory_badges(run, sources)}{_memory_timeline(run, sources)}"
            f"<h3>Largest accumulated memory growth</h3>"
            f"{table}</section>"
        )
    return (
        '<section id="memory" class="page" hidden><h1>Memory</h1>'
        + "".join(sections)
        + "</section>"
    )


def render_html(result: ProfileResult, *, root: str | Path | None = None) -> str:
    """Render an offline report without exposing backend-specific structures.

    Source and metadata are escaped. The document performs no network
    requests. Unknown values use an em dash; sampled estimates never invent
    hit counts.

    Parameters
    ----------
    result : [ProfileResult]
        Normalized measurements and exact source snapshots.

    root : str | Path | None, default=None
        Launch directory retained for existing callers. Python source labels
        always show the filename.

    Returns
    -------
    str
        A single HTML document containing all source, styles, and navigation.

    """
    runs = _walk(result.root_run)
    data = _combined_lines(runs)
    lines = list(data.values())
    lines_by_source: dict[str, list[LineStats]] = {}
    for line in lines:
        lines_by_source.setdefault(line.location.source_id, []).append(line)
    capabilities = _source_capabilities(result, runs)
    mixed = any(
        run.metadata.get("child_backend", result.backend) != result.backend for run in runs
    )
    sampled = result.capabilities.sampled or any(item.sampled for item in capabilities.values())
    count_header = "Samples" if sampled else None
    functions = [function for run in runs for function in run.functions]
    executions = [execution for run in runs for execution in run.spark_executions]
    measured = [
        line for line in lines if line.wall_time_ns is not None or line.hits or line.samples
    ]
    maximum = max((line.wall_time_ns or 0 for line in lines), default=0)
    source_lengths = {unit.id: len(unit.source.splitlines()) for unit in result.sources.values()}
    maximum_memory = max(
        (
            max(line.ram.delta_bytes or 0, 0)
            for line in lines
            if line.ram is not None
            and capabilities.get(line.location.source_id, result.capabilities).memory
            and 0 < line.location.line <= source_lengths.get(line.location.source_id, 0)
        ),
        default=0,
    )
    # Source identities retain their paths; display labels need only filenames.
    del root
    sources = []
    for unit in result.sources.values():
        label = _source_name(unit)
        sources.append(replace(unit, path=label))
    source_names = {unit.id: unit.path for unit in sources}
    source_groups = _source_groups(sources)
    has_notebooks = any(unit.kind == SourceKind.NOTEBOOK for unit in sources) or len(runs) > 1
    contexts = ["function"]
    if has_notebooks:
        contexts.append("notebook")
    if executions:
        contexts.append("Spark plan")

    intro = f"Follow the work from a source line to its {' or '.join(contexts)}."
    cards = [
        ("Elapsed wall time", _time(result.root_run.elapsed_ns)),
        ("Source snapshots", str(len(source_groups))),
        ("Functions", str(len(functions))),
        (
            "Observed lines" if sampled else "Executed lines",
            str(len(measured)),
        ),
    ]
    if executions:
        cards.append(("Spark executions", str(len(executions))))
    if sampled:
        total_samples = _total([line.samples for line in lines])
        sampling_capabilities = [item for item in capabilities.values() if item.sampled]
        if total_samples is None and all(
            item.sample_counts for item in sampling_capabilities or [result.capabilities]
        ):
            total_samples = 0
        cards.append(("Samples", _count(total_samples)))
    if result.capabilities.sampled and "sample_rate" in result.root_run.metadata:
        rate_label = escape(_metric_value(result.root_run.metadata["sample_rate"]))
        cards.append(("Target samples / sec", rate_label))

    card_html = "".join(
        f'<div class="stat"><span>{escape(label)}</span><strong>{value}</strong></div>'
        for label, value in cards
    )
    slowest = sorted(measured, key=lambda line: line.wall_time_ns or 0, reverse=True)[:10]
    hot_rows = []

    for line in slowest:
        unit = result.sources.get(line.location.source_id)
        source_line = ""

        if unit and 0 < line.location.line <= len(unit.source.splitlines()):
            source_line = unit.source.splitlines()[line.location.line - 1].strip()

        filename = source_names.get(line.location.source_id, line.location.source_id)
        label = f"{filename}:{line.location.line}"
        hot_rows.append(
            [
                _time(line.wall_time_ns),
                (
                    f'<a href="{_source_link(line.location.source_id, line.location.line)}'
                    f'">{escape(label)}</a>'
                ),
                *([_count(line.samples)] if sampled else []),
                f"<code>{escape(source_line)}</code>",
            ]
        )

    function_rows = [
        [
            _time(item.total_time_ns),
            (
                f'<a href="{_source_link(item.source_id, item.first_line)}">'
                f"{escape(source_names.get(item.source_id, item.source_id))}:{item.first_line}</a>"
            ),
            _count(item.samples if sampled else item.calls),
            (
                f'<a href="{_source_link(item.source_id, item.first_line)}'
                f'">{escape(item.qualified_name)}()</a>'
            ),
            _count(item.line_count),
        ]
        for item in functions
    ]
    function_values: list[dict[str, str | int | None]] = [
        {
            "name": item.qualified_name,
            "line": item.first_line,
            "lines": item.line_count,
            "time": item.total_time_ns,
            "count": item.samples if sampled else item.calls,
        }
        for item in functions
    ]
    hot_table = _table(_profile_headers(count_header), hot_rows, css="hot-lines")
    pages = [
        (
            f'<section id="overview" class="page"><h1>Overview</h1>'
            f'<p class="intro">{intro}</p><div'
            f' class="stats overview-stats">{card_html}</div><div class="section-heading">'
            f"<h2>Most expensive"
            f' lines</h2><a href="#files">Explore all sources <span aria-hidden="true">↗</span>'
            f"</a></div>"
            f"{hot_table}"
            f"</section>"
        )
    ]
    has_memory = any(run.memory_samples for run in runs)
    if has_memory:
        pages.append(_memory_page(runs, result.sources))
    file_rows = []
    file_values: list[dict[str, str | int | None]] = []
    source_times: dict[str, int | None] = {}
    for units in source_groups:
        unit = units[0]
        label = _source_name(unit, include_cell=False)
        unit_lines = [line for item in units for line in lines_by_source.get(item.id, [])]
        duration = _total([line.wall_time_ns for line in unit_lines])
        source_times[unit.id] = duration
        samples = _total(
            [
                (
                    sum(line.samples or 0 for line in lines_by_source.get(item.id, []))
                    if capabilities.get(item.id, result.capabilities).sample_counts
                    else _total([line.samples for line in lines_by_source.get(item.id, [])])
                )
                for item in units
            ]
        )
        kind = "Notebook" if unit.kind == SourceKind.NOTEBOOK else "Python"
        length = sum(len(item.source.splitlines()) for item in units)
        file_rows.append(
            [
                _time(duration),
                f'<a href="{_source_link(unit.id)}">{escape(label)}</a>',
                *([_count(samples)] if sampled else []),
                kind,
                str(length),
            ]
        )
        file_values.append(
            {
                "name": label,
                "kind": kind,
                "lines": length,
                "time": duration,
                "samples": samples,
            }
        )
    file_headers = [*_profile_headers(count_header, source=False), "Kind", "Lines"]
    file_columns = list(
        zip(
            file_headers,
            ["time", "name", *(["samples"] if sampled else []), "kind", "lines"],
            strict=True,
        )
    )
    child_rows = [
        [
            f'<a href="#{_key("run", run.id)}">{escape(run.name)}</a>',
            _time(run.metadata.get("parent_wait_time_ns", run.elapsed_ns)),
            escape(run.status),
        ]
        for run in runs[1:]
    ]
    invocations = (
        "<h2>Notebook invocations</h2>"
        + _table(["Notebook", "Parent wait", "Status"], child_rows, css="notebook-invocations")
        if child_rows
        else ""
    )
    pages.append(
        f'<section id="files" class="page" hidden><div class="summary-header"><h1>Files</h1>'
        f"{_heat_controls(enabled=False)}</div>"
        f'<p class="muted">Source snapshots stay'
        f" readable even after your code changes."
        f"</p>{_summary_table(file_columns, file_rows, file_values)}"
        f"{invocations}</section>"
    )
    function_headers = [*_profile_headers("Samples" if sampled else "Calls"), "Lines"]
    function_columns = list(
        zip(function_headers, ["time", "line", "count", "name", "lines"], strict=True)
    )
    function_estimate = " Sampling reports estimate this time." if sampled else ""
    pages.append(
        f'<section id="functions" class="page" hidden><div class="summary-header">'
        f"<h1>Functions</h1>"
        f"{_heat_controls(enabled=False)}</div>"
        f'<p class="muted">Time spent on each function\'s own lines, excluding time'
        f" in other project functions it calls.{function_estimate} Select a function to open"
        f" its definition. The line count includes the definition and body, with blank lines"
        f" and comments."
        f"</p>{_summary_table(function_columns, function_rows, function_values)}</section>"
    )
    for run in runs[1:]:
        source = (
            f'<a href="{_source_link(run.source.id)}">Open captured notebook source ↗</a>'
            if run.source
            else ""
        )
        wait_time = run.metadata.get("parent_wait_time_ns", run.elapsed_ns)
        child_note = (
            (
                "Child profile merged. Its captured sources and measurements are included in"
                " this report."
            )
            if run.metadata.get("collection") == NotebookCollection.MERGED
            else (
                "Child source is included. Line measurements were unavailable; see the report"
                " warnings for the collection limitation."
                if run.metadata.get("collection") == NotebookCollection.SOURCE_ONLY
                else "Child line measurements require instrumentation in that child environment."
            )
        )
        pages.append(
            f'<section id="{_key("run", run.id)}" class="page" hidden>'
            f"<h1>{escape(run.name)}</h1>"
            f"<p>Parent wait: <strong>{_time(wait_time)}</strong> · {escape(run.status)}"
            f'</p><p>{source}</p>{_metadata(run.metadata)}<p class="muted">{child_note}'
            f"</p></section>"
        )

    if executions:
        spark_rows = []
        for execution in executions:
            cost = _SparkCost(
                execution.stats.wall_time_ns,
                execution.stats.peak_memory_bytes,
                execution.stats.spill_bytes,
                "Action wall time",
            )
            status = (
                f'<small class="spark-secondary">{escape(execution.status)}</small>'
                if execution.status != "success"
                else ""
            )
            spark_rows.append(
                (
                    [
                        (
                            f'<a href="#{_key("spark", execution.id)}">{escape(execution.name)} '
                            f"<small>#{escape(execution.id[:8])}</small></a>{status}"
                        ),
                        _spark_trigger(execution.location, result.sources),
                        _spark_cost_cells(cost)[0],
                        _time(execution.stats.executor_time_ns),
                        *_spark_cost_cells(cost)[1:],
                    ],
                    cost,
                )
            )

        spark_table = _spark_cost_table(
            "Most expensive actions",
            ["Action", "Source", "Wall time", "Executor time", "Peak memory", "Spill"],
            spark_rows,
            css="spark-actions",
        )
        operator_table = _spark_cost_table(
            "Most expensive operators",
            ["Operation", "Action / source", "Operator time", "Peak memory", "Spill"],
            _spark_operator_rows(executions, result.sources, context=True),
            css="spark-operators",
        )
        ordered_executions = sorted(
            executions,
            key=lambda execution: (
                execution.stats.wall_time_ns is None,
                -(execution.stats.wall_time_ns or 0),
            ),
        )
        choices = "".join(
            f'<option value="{index}">{escape(execution.name)} #{escape(execution.id[:8])}'
            f" · {_time(execution.stats.wall_time_ns)}</option>"
            for index, execution in enumerate(ordered_executions)
        )
        overviews = "".join(
            f'<div class="spark-action-overview" data-action="{index}"'
            f"{' hidden' if index else ''}>"
            f'<p class="spark-overview-context">'
            f'<a href="#{_key("spark", execution.id)}">Open action details ↗</a>'
            f" · {_spark_source_link(execution.location, result.sources)}</p>"
            f"{_spark_plan_overview(execution)}</div>"
            for index, execution in enumerate(ordered_executions)
        )
        pages.append(
            f'<section id="spark" class="page" hidden><h1>Spark</h1>'
            '<p class="intro">Follow the data through the main steps and see where reported'
            " time, memory, or row growth stands out.</p>"
            f'<div class="spark-action-picker"><label for="spark-action-select">'
            f'Plan overview for</label><select id="spark-action-select">{choices}</select></div>'
            f'{overviews}<details class="execution-details"><summary>Compare all actions</summary>'
            f'{spark_table}</details><details class="execution-details">'
            f"<summary>All operator costs</summary>"
            f"{operator_table}</details></section>"
        )

    for execution in executions:
        trigger = _spark_trigger(execution.location, result.sources)
        plan_views = []
        plan_buttons = []

        plans = [
            ("Executed plan", execution.executed_plan),
            ("Initial plan", execution.initial_plan),
            ("Optimized logical plan", execution.optimized_plan),
            ("Parsed logical plan", execution.parsed_plan),
            ("Analyzed logical plan", execution.analyzed_plan),
        ]
        plans = [
            (label, plan) for label, plan in plans if plan is not None or label == "Executed plan"
        ]
        selected_plan = next(
            (index for index, (_, plan) in enumerate(plans) if plan is not None), 0
        )

        for index, (label, plan) in enumerate(plans):
            plan_buttons.append(
                f'<button type="button" class="plan-tab" data-tab="{index}"'
                f' aria-pressed="{"true" if index == selected_plan else "false"}">{label}</button>'
            )
            plan_views.append(
                f'<pre class="plan-view" data-tab-panel="{index}'
                f'"{"" if index == selected_plan else " hidden"}'
                f">{escape(plan or 'Executed plan unavailable in this environment.')}"
                f"</pre>"
            )

        operators = (
            '<ul class="operator-tree">'
            + "".join(_operator(operator, execution.id) for operator in execution.operators)
            + "</ul>"
            if execution.operators
            else '<p class="muted">Operator details unavailable.</p>'
        )
        stats = execution.stats
        metrics = {
            "Jobs": len(execution.jobs),
            "Status": execution.status,
            "Rows": stats.rows,
            "Read": _bytes(stats.bytes_read),
            "Shuffle read": _bytes(stats.shuffle_read_bytes),
            "Shuffle write": _bytes(stats.shuffle_write_bytes),
            "Spill": _bytes(stats.spill_bytes),
        }
        metric_html = _metadata(
            {key: value for key, value in metrics.items() if value is not None and value != "—"}
        )
        details = (
            "".join(
                (
                    f"<details><summary>Stage {escape(str(stage.get('id', index)))}"
                    f"</summary>{_metadata(stage)}</details>"
                )
                for index, stage in enumerate(execution.stages)
            )
            or '<p class="muted">Stage and task statistics unavailable.</p>'
        )
        notes = "".join(f'<p class="notice">{escape(note)}</p>' for note in execution.warnings)
        collection = dict(execution.metadata)

        if "query_wall_time_ns" in collection:
            collection["Query wall time (JVM listener)"] = _time(
                collection.pop("query_wall_time_ns")
            )

        collection_notes = (
            (
                f'<details class="execution-details spark-collection-notes">'
                f"<summary>Plan provenance and collection notes"
                f"</summary>{notes}{_metadata(collection)}</details>"
            )
            if collection or notes
            else ""
        )
        cost_cards = "".join(
            f'<div class="stat"><span>{label}</span><strong>{value}</strong></div>'
            for label, value in (
                ("Wall time", _time(stats.wall_time_ns)),
                ("Cumulative executor time", _time(stats.executor_time_ns)),
                ("Executor peak memory", _bytes(stats.peak_memory_bytes)),
            )
        )
        operator_table = _spark_cost_table(
            "Most expensive operators",
            ["Operation", "Operator time", "Peak memory", "Spill"],
            _spark_operator_rows([execution], result.sources),
            css="spark-operators",
        )
        pages.append(
            f'<section id="{_key("spark", execution.id)}" class="page" hidden>'
            f"<h1>{escape(execution.name)}</h1>"
            f'<p class="spark-trigger">{trigger}</p><div class="stats spark-stats">'
            f"{cost_cards}</div>"
            f"{_spark_plan_overview(execution)}"
            f'<details class="execution-details spark-plans"><summary>Query plans</summary>'
            f'<div class="plan-tabs"'
            f' role="group" aria-label="Plan views">{"".join(plan_buttons)}'
            f"</div>{''.join(plan_views)}</details>"
            f'<details class="execution-details"><summary>Physical operator tree and all metrics'
            f"</summary>{operators}</details><details"
            f' class="execution-details"><summary>Action metrics</summary>{metric_html}</details>'
            f"{collection_notes}<details"
            f' class="execution-details"><summary>Stage and task details'
            f"</summary>{details}</details>"
            f'<details class="execution-details"><summary>Operator cost ranking</summary>'
            f"{operator_table}</details></section>"
        )

    pages.extend(
        _source_page(
            units,
            data,
            maximum,
            maximum_memory,
            capabilities,
            result.capabilities,
            source_times[units[0].id],
        )
        for units in source_groups
    )
    source_items = []
    for units in source_groups:
        unit = units[0]
        label = _source_name(unit, include_cell=False)
        icon = _notebook_icon() if unit.kind == SourceKind.NOTEBOOK else "⌘"
        source_items.append(
            f'<a class="source-item" href="{_source_link(unit.id)}"'
            f' title="{escape(label, quote=True)}"><span'
            f' class="file-icon" aria-hidden="true">{icon}'
            f"</span>{escape(label)}</a>"
        )
    source_nav = "".join(source_items)
    views = [
        ("overview", "◈", "Overview"),
        ("files", "▤", "Files"),
        ("functions", "ƒ", "Functions"),
    ]
    if has_memory:
        views.append(
            (
                "memory",
                (
                    '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
                    ' stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
                    '<rect x="5" y="7" width="14" height="10" rx="2"></rect>'
                    '<path d="M8 4v3m4-3v3m4-3v3M8 17v3m4-3v3m4-3v3'
                    'M2 10h3m-3 4h3m14-4h3m-3 4h3M9 10h6v4H9z"></path></svg>'
                ),
                "Memory",
            ),
        )
    if executions:
        views.append(("spark", "✧", "Spark"))

    navigation = "".join(
        f'<a href="#{page}" class="nav-link"><span aria-hidden="true">{icon}</span>{label}</a>'
        for page, icon, label in views
    )
    css = files("linescope.render").joinpath("assets/styles.css").read_text(encoding="utf-8")
    javascript = files("linescope.render").joinpath("assets/app.js").read_text(encoding="utf-8")
    logo = b64encode(files("linescope.render").joinpath("assets/icon.svg").read_bytes()).decode(
        "ascii"
    )
    mode = "Sampling" if result.capabilities.sampled else "Tracing"

    if mixed:
        mode = "Mixed collection"

    guide = "https://tvdboom.github.io/linescope/user_guide"
    backend_section = (
        str(result.backend)
        if result.backend in {Backend.TRACE, Backend.SCALENE, Backend.TACHYON}
        else "custom-backends"
    )
    header_details = "".join(
        f'<a class="header-detail" href="{guide}/{path}" target="_blank"'
        f' rel="noopener noreferrer" title="Read about {label.lower()} in the user guide'
        f' (opens in a new tab)"><span class="header-label">{label}</span>'
        f'<span class="header-value">{value}</span></a>'
        for label, value, path in (
            (
                "Run status",
                (
                    '<span class="status-dot" aria-hidden="true"></span>'
                    f"{escape(result.root_run.status.title())}"
                ),
                "reports/#run-status",
            ),
            ("Backend", escape(result.backend), f"backends/#{backend_section}"),
            ("Collection method", mode, "reports/#collection-method"),
        )
    )

    return (
        f"""<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta"""
        f""" name="viewport" content="width=device-width, initial-scale=1"><meta"""
        f""" http-equiv="Content-Security-Policy" content="default-src 'none'; style-src"""
        f""" 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src 'none';"""
        f""" base-uri 'none'; form-action 'none'"><title>LineScope · Source profile"""
        '</title><link rel="icon" type="image/svg+xml" sizes="any"'
        f""" href="data:image/svg+xml;base64,{logo}"><style>{css}</style></head>\n"""
        f"""<body><a class="skip-link\""""
        f""" href="#main">Skip to report</a><aside class="sidebar"><a class="brand\""""
        f""" href="#overview"><img class="brand-mark\""""
        f""" src="data:image/svg+xml;base64,{logo}" alt=""><span>Line<span"""
        f""" class="brand-accent">Scope</span></span></a><div class="run-label">REPORT"""
        f""" NAVIGATION</div><nav aria-label="Report views">{navigation}</nav><div"""
        f""" class="source-nav"><label for="source-search">SOURCE SNAPSHOTS</label><input"""
        f""" id="source-search" type="search" placeholder="Find a source…\""""
        f""" autocomplete="off">{source_nav or '<p class="muted">No source captured</p>'}"""
        f"""</div></aside><div class="workspace"><header class="topbar"><div"""
        f' class="header-details">{header_details}</div><button id="theme-toggle"'
        f' type="button" aria-label="Switch color theme">◐</button></header><main id="main"'
        f""" tabindex="-1">{"".join(pages)}</main></div><script>{javascript}</script>"""
        f"""</body></html>"""
    )
