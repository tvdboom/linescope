"""LineScope.

Author: Mavs
Description: Self-contained, escaped, accessible source profiling reports.

"""

from __future__ import annotations

from base64 import b64encode
from copy import deepcopy
from dataclasses import dataclass, replace
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

    path = (
        PurePath(unit.path.replace("\\", "/")).name
        if unit.kind == SourceKind.PYTHON
        else unit.path
    )
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

            name = f"Cumulative operator time · {name}"
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


@dataclass(frozen=True)
class _SparkCost:
    """Preserve comparable Spark costs without inventing missing metrics.

    Attributes
    ----------
    time_ns : int | None
        Largest reported operator timing in nanoseconds, if available.

    peak_memory_bytes : int | None
        Largest explicitly labeled peak memory measurement, if available.

    spill_bytes : int | None
        Largest reported spill measurement in bytes, if available.

    time_label : str
        Name of the selected timing metric used in report tooltips.

    """

    time_ns: int | None = None
    peak_memory_bytes: int | None = None
    spill_bytes: int | None = None
    time_label: str = ""


def _operator_cost(operator: SparkOperator) -> _SparkCost:
    """Select reported costs without adding overlapping SQL metrics.

    Multiple timings on one node can overlap. Use the largest reported
    timing, preserving its name, and never derive action totals from it.
    Byte counters for data size, shuffle, and spill are not peak memory.

    """
    times: list[tuple[int, str]] = []
    memory: list[int] = []
    spills: list[int] = []
    for key, metric in operator.metrics.items():
        structured = isinstance(metric, dict)
        name = str(metric.get("name") or key) if structured else str(key)
        kind = str(metric.get("type", "")).lower() if structured else ""
        value = metric.get("value") if structured else metric
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(value)
            or value < 0
        ):
            continue

        category = f"{key} {name}".lower()
        if kind in ("timing", "nstiming") or (not structured and str(key).endswith("_ns")):
            times.append((int(value * (1_000_000 if kind == "timing" else 1)), name))
        elif kind == "size" or (not structured and "byte" in category):
            if "spill" in category:
                spills.append(int(value))
            elif "memory" in category and "peak" in category:
                memory.append(int(value))

    duration, label = max(times, key=lambda item: item[0]) if times else (None, "")
    return _SparkCost(duration, max(memory, default=None), max(spills, default=None), label)


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


def _source_page(
    unit: SourceUnit,
    data: dict[tuple[str, int], LineStats],
    maximum: int,
    maximum_memory: int,
    capabilities: BackendCapabilities,
) -> str:
    """Render complete captured source with line metrics and navigation.

    Keep unknown measurements, heat scaling, and optional memory or GPU columns
    consistent.

    Parameters
    ----------
    unit : [SourceUnit]
        Captured source snapshot being inspected.

    data : dict[tuple[str, int], LineStats]
        Normalized or serialized values used by this operation.

    maximum : int
        Largest visible line duration used to scale source heat.

    maximum_memory : int
        Largest visible positive process-memory change, in bytes, used to
        scale memory-growth heat.

    capabilities : [BackendCapabilities]
        Measurements supported by the relevant collector.

    Returns
    -------
    str
        Complete source page markup with available measurements.

    """
    page = _key("source", unit.id)
    spans = _tokens(unit.source)
    memory = capabilities.memory
    gpu = capabilities.gpu
    source_lines = unit.source.splitlines() or [""]
    source_stats = [data.get((unit.id, number)) for number in range(1, len(source_lines) + 1)]
    # Preserve known counts on sources shared by collectors with different capabilities.
    hit_counts = capabilities.hit_counts or any(
        stats is not None and stats.hits is not None for stats in source_stats
    )
    sample_counts = capabilities.sample_counts or any(
        stats is not None and stats.samples is not None for stats in source_stats
    )
    context = any(
        stats is not None and (stats.spark_executions or stats.notebook_runs)
        for stats in source_stats
    )
    rows = []

    for number, (text, stats) in enumerate(zip(source_lines, source_stats, strict=True), 1):
        duration = stats.wall_time_ns if stats else None
        ram = stats.ram if memory and stats else None
        delta = ram.delta_bytes if ram else None
        hits = stats.hits if stats else (0 if capabilities.hit_counts else None)

        if hits is None and capabilities.hit_counts and duration is None:
            hits = 0

        average = None if duration is None or not hits else duration // hits
        samples = stats.samples if stats else None
        if samples is None and capabilities.sample_counts and (stats is None or duration is None):
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

        if memory:
            peak = ram.peak_bytes if ram else None
            allocation = stats.memory.delta_bytes if stats and stats.memory else None
            memory_cells = (
                f'<td class="metric">{_bytes(delta, signed=True)}</td>'
                f'<td class="metric">{_bytes(peak)}</td>'
            )
        gpu_cells = ""
        if gpu:
            gpu_cells = (
                f"<td"
                f' class="metric">{_time(stats.gpu.time_ns if stats and stats.gpu else None)}'
                f"</td><td"
                f' class="metric">'
                f"{_bytes(stats.gpu.peak_memory_bytes if stats and stats.gpu else None)}</td>"
            )

        rows.append(
            f'<tr id="{page}-L{number}" class="source-row" style="--heat:{intensity:.5f}"'
            f' data-heat-time="{intensity:.5f}" data-heat-memory="{memory_intensity:.5f}"'
            f' data-line="{number}" data-time="{"" if duration is None else duration}"'
            f' data-memory="{"" if delta is None else delta}"'
            f' data-allocation="{"" if allocation is None else allocation}">'
            f'<td class="line-number"><a href="#{page}-L{number}" aria-label="Line'
            f' {number}">{number}</a></td><td class="metric">{_time(duration)}</td>'
            f"{hit_cells}{sample_cell}{memory_cells}{gpu_cells}<td"
            f' class="source-code"><code>{_code(text, spans.get(number, []), stats)}'
            f"</code></td>{context_cell}</tr>"
        )

    memory_head = (
        '<th scope="col" title="Process memory changes across executions of this line">'
        'Mem Change</th><th scope="col" title="Highest process memory observed during this line">'
        "Peak Mem</th>"
        if memory
        else ""
    )
    gpu_head = "<th>Estimated GPU time</th><th>GPU peak memory</th>" if gpu else ""
    hit_head = '<th scope="col">Hits</th><th scope="col">Avg / hit</th>' if hit_counts else ""
    sample_head = '<th scope="col">Samples</th>' if sample_counts else ""
    context_head = '<th scope="col">Context</th>' if context else ""
    memory_order = (
        '<button type="button" class="source-order" data-order="memory" aria-pressed="false"'
        ' title="Order by accumulated process memory change, highest first">Mem Growth</button>'
        if memory
        else ""
    )
    memory_heat = (
        '<button type="button" class="source-heat" data-heat="memory" aria-pressed="false"'
        ' title="Color by positive accumulated process memory change">Mem Growth</button>'
        if memory
        else ""
    )
    return (
        f'<section id="{page}" class="page source-page" hidden><div class="source-header">'
        f'<h1 id="{page}-title" class="path-title">{escape(unit.path)}</h1>'
        f'<div class="source-summary"><p class="muted">{len(source_lines)} lines</p>'
        f'<div class="source-controls"><div class="source-toolbar"><span>Heatmap by</span>'
        f'<div class="source-heat-controls" role="group" aria-label="Color source lines">'
        f'<button type="button" class="source-heat" data-heat="time" aria-pressed="true"'
        f' title="Color by time">Time</button>{memory_heat}</div></div>'
        f'<div class="source-toolbar"><span>Order lines by</span>'
        f'<div class="source-order-controls" role="group" aria-label="Order source lines">'
        f'<button type="button" class="source-order" data-order="line" aria-pressed="true"'
        f' title="Order by line number">Line number</button>'
        f'<button type="button" class="source-order" data-order="time" aria-pressed="false"'
        f' title="Order by time, highest first">Time</button>{memory_order}'
        f"</div></div></div></div></div>"
        f'<div class="source-scroll" tabindex="0" role="region"'
        f' aria-labelledby="{page}-title"><table'
        f' class="source-table"><thead><tr><th scope="col">Line</th><th'
        f' scope="col">{"Estimated time" if capabilities.sampled else "Python time"}</th>'
        f'{hit_head}{sample_head}{memory_head}{gpu_head}<th scope="col">Source</th>'
        f"{context_head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
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
        return "Baseline / outside project"
    name = PurePath(unit.path.replace("\\", "/")).name
    return f'<a href="{_source_link(unit.id, location.line)}">{escape(name)}:{location.line}</a>'


def _memory_timeline(run: ProfileRun, sources: dict[str, SourceUnit]) -> str:
    """Render a run's RAM timeline without combining separate processes.

    Parameters
    ----------
    run : [ProfileRun]
        Run containing chronological process RAM observations.

    sources : dict[str, [SourceUnit]]
        Snapshots available for point and table navigation.

    Returns
    -------
    str
        Interactive SVG chart and accessible retained-reading table.

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
    rows = []
    for index, sample in enumerate(samples):
        location = _memory_location(sample.location, sources)
        rows.append([_time(sample.elapsed_ns), _bytes(sample.rss_bytes), location])
        if sample.rss_bytes is None:
            if segment:
                segments.append(" ".join(segment))
                segment = []
            continue
        x = 65 + 910 * sample.elapsed_ns / duration
        y = 250 - 225 * sample.rss_bytes / scale
        segment.append(f"{x:.2f},{y:.2f}")
        label = f"{_time(sample.elapsed_ns)} · {_bytes(sample.rss_bytes)}"
        point = (
            f'<circle class="memory-point{" selected" if index == peak_index else ""}"'
            f' cx="{x:.2f}" cy="{y:.2f}" r="3" data-index="{index}"'
            f' data-label="{escape(label, quote=True)}"><title>{escape(label)}</title></circle>'
        )
        if location.startswith("<a") and sample.location is not None:
            point = (
                f'<a href="{_source_link(sample.location.source_id, sample.location.line)}"'
                f' aria-label="Open source for {escape(label, quote=True)}">{point}</a>'
            )
        points.append(point)
    if segment:
        segments.append(" ".join(segment))
    plot = "".join(f'<polyline points="{segment}"/>' for segment in segments)
    ticks = "".join(
        f'<text x="58" y="{y + 4}" text-anchor="end">{_bytes(value)}</text>'
        f'<line x1="65" y1="{y}" x2="975" y2="{y}"/>'
        for y, value in ((25, peak), (137.5, peak // 2), (250, 0))
    )
    ticks += "".join(
        f'<text x="{x}" y="280" text-anchor="middle">{_time(value)}</text>'
        for x, value in ((65, 0), (520, duration // 2), (975, duration))
    )
    cursor_id = _key("memory-cursor", run.id)
    compressed = (
        "Timeline compressed; bucket peaks, troughs, and endpoints are retained."
        if run.metadata.get("memory_timeline_compressed")
        else "Line-boundary readings and periodic observations during long calls."
    )
    return (
        f'<div class="memory-inspector"><p class="muted">{compressed}</p>'
        f'<svg class="memory-chart" viewBox="0 0 1000 300" role="img"'
        f' aria-label="Process RAM over elapsed time; observed peak {_bytes(peak)}">'
        f'<g class="memory-axis">{ticks}</g><g class="memory-plot">{plot}</g>'
        f'{"".join(points)}</svg><div class="memory-controls">'
        f'<label for="{cursor_id}">Inspect reading</label>'
        f'<input id="{cursor_id}" class="memory-cursor" type="range" min="0"'
        f' max="{len(samples) - 1}" value="{peak_index}">'
        f'<output class="memory-reading" for="{cursor_id}">'
        f"{_time(samples[peak_index].elapsed_ns)} · {_bytes(peak)} · "
        f"{_memory_location(samples[peak_index].location, sources)}</output></div>"
        f"<details><summary>Retained readings ({len(samples):,})</summary>"
        f"{_table(['Elapsed', 'Process RAM', 'Source'], rows)}</details></div>"
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
    for run in runs:
        if not run.memory_samples:
            continue
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
            ]
            for line in growth[:10]
            if line.ram is not None and line.ram.delta_bytes is not None
        ]
        sections.append(
            f"<h2>{escape(run.name)}</h2>{_memory_timeline(run, sources)}"
            f"<h3>Largest accumulated memory growth</h3>"
            f"{_table(['Mem Change', 'Peak Mem', 'Source'], rows)}"
        )
    return (
        '<section id="memory" class="page" hidden><h1>Memory</h1>'
        '<p class="intro">Follow process RAM over time and open the source at a spike.</p>'
        '<p class="semantics">RSS is resident RAM for the whole Python process, including '
        "native libraries and profiler overhead. Other threads can change it. Mem Change "
        "sums observed line intervals; Peak Mem is the highest reading during a line; peaks are "
        "observed, so brief spikes between readings can be missed. Child process timelines "
        "are shown separately.</p>" + "".join(sections) + "</section>"
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
    capabilities = _source_capabilities(result, runs)
    mixed = any(
        run.metadata.get("child_backend", result.backend) != result.backend for run in runs
    )
    sampled = result.capabilities.sampled or any(item.sampled for item in capabilities.values())
    sample_headers = ["Samples"] if sampled else []
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
        label = (
            PurePath(unit.path.replace("\\", "/")).name
            if unit.kind == SourceKind.PYTHON
            else unit.path
        )
        sources.append(replace(unit, path=label))
    source_names = {unit.id: unit.path for unit in sources}
    has_notebooks = any(unit.kind == SourceKind.NOTEBOOK for unit in sources) or len(runs) > 1
    contexts = ["function"]
    if has_notebooks:
        contexts.append("notebook")
    if executions:
        contexts.append("Spark plan")

    intro = f"Follow the work from a source line to its {' or '.join(contexts)}."
    cards = [
        ("Elapsed wall time", _time(result.root_run.elapsed_ns)),
        ("Source snapshots", str(len(sources))),
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

        filename = PurePath(
            source_names.get(line.location.source_id, line.location.source_id)
        ).name
        label = f"{filename}:{line.location.line}"
        hot_rows.append(
            [
                f'<strong class="hot-time">{_time(line.wall_time_ns)}</strong>',
                (
                    f'<a href="{_source_link(line.location.source_id, line.location.line)}'
                    f'">{escape(label)}</a>'
                ),
                f"<code>{escape(source_line)}</code>",
                *([_count(line.samples)] if sampled else []),
            ]
        )

    function_rows = [
        [
            _time(item.total_time_ns),
            _count(item.samples if sampled else item.calls),
            (
                f'<a href="{_source_link(item.source_id, item.first_line)}'
                f'">{escape(item.qualified_name)}()</a>'
            ),
        ]
        for item in sorted(functions, key=lambda item: item.total_time_ns or 0, reverse=True)
    ]
    hot_table = _table(["Time", "Location", "Source", *sample_headers], hot_rows, css="hot-lines")
    pages = [
        (
            f'<section id="overview" class="page"><h1>Overview</h1>'
            f'<p class="intro">{intro}</p><div'
            f' class="stats">{card_html}</div><div class="section-heading">'
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
    file_rows = [
        [
            f'<a href="{_source_link(unit.id)}">{escape(unit.path)}</a>',
            "Notebook" if unit.kind == SourceKind.NOTEBOOK else "Python",
            str(len(unit.source.splitlines())),
            _time(
                _total([line.wall_time_ns for line in lines if line.location.source_id == unit.id])
            ),
            *(
                [
                    _count(
                        _total(
                            [line.samples for line in lines if line.location.source_id == unit.id]
                        )
                        if not capabilities.get(unit.id, result.capabilities).sample_counts
                        else sum(
                            line.samples or 0
                            for line in lines
                            if line.location.source_id == unit.id
                        )
                    )
                ]
                if sampled
                else []
            ),
        ]
        for unit in sources
    ]
    file_headers = ["Source", "Kind", "Lines", "Measured time", *sample_headers]
    pages.append(
        f'<section id="files" class="page" hidden><h1>Files</h1>'
        f'<p class="muted">Source snapshots stay'
        f" readable even after your code changes."
        f"</p>{_table(file_headers, file_rows)}</section>"
    )
    function_headers = ["Self time", "Samples" if sampled else "Calls", "Function"]
    pages.append(
        f'<section id="functions" class="page" hidden><h1>Functions</h1>'
        f'<p class="muted">Time on the function\'s own lines, excluding nested'
        f" project callees. Select a function to open its definition."
        f"</p>{_table(function_headers, function_rows)}</section>"
    )
    notebook_rows = [
        row
        for unit, row in zip(sources, file_rows, strict=True)
        if unit.kind == SourceKind.NOTEBOOK
    ]
    child_rows = [
        [
            f'<a href="#{_key("run", run.id)}">{escape(run.name)}</a>',
            _time(run.metadata.get("parent_wait_time_ns", run.elapsed_ns)),
            escape(run.status),
        ]
        for run in runs[1:]
    ]
    if has_notebooks:
        notebook_headers = ["Snapshot", "Kind", "Lines", "Measured time", *sample_headers]
        pages.append(
            f'<section id="notebooks" class="page" hidden><h1>Notebooks'
            f"</h1>{_table(notebook_headers, notebook_rows)}"
            f"<h2>Notebook invocations"
            f"</h2>{_table(['Notebook', 'Parent wait', 'Status'], child_rows)}</section>"
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
        pages.append(
            f'<section id="spark" class="page" hidden><h1>Spark</h1>'
            f'<p class="muted">Start with the largest costs. Switch to Memory or Spill to find'
            f" what used the most bytes. Select an action for its plans and details.</p>"
            f"{spark_table}{operator_table}"
            f"</section>"
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
            f"{operator_table}"
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
            f"</section>"
        )

    pages.extend(
        _source_page(
            unit, data, maximum, maximum_memory, capabilities.get(unit.id, result.capabilities)
        )
        for unit in sources
    )
    source_nav = "".join(
        (
            f'<a class="source-item" href="{_source_link(unit.id)}"'
            f' title="{escape(unit.path, quote=True)}"><span'
            f' class="file-icon">{"▤" if unit.kind == SourceKind.NOTEBOOK else "⌘"}'
            f"</span>{escape(PurePath(unit.path).name)}</a>"
        )
        for unit in sources
    )
    views = [
        ("overview", "◈", "Overview"),
        ("files", "▤", "Files"),
        ("functions", "ƒ", "Functions"),
    ]
    if has_memory:
        views.append(("memory", "▥", "Memory"))
    if has_notebooks:
        views.append(("notebooks", "▦", "Notebooks"))
    if executions:
        views.append(("spark", "✧", "Spark"))

    navigation = "".join(
        f'<a href="#{page}" class="nav-link"><span>{icon}</span>{label}</a>'
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
        f"""</title><style>{css}</style></head>\n<body><a class="skip-link\""""
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
