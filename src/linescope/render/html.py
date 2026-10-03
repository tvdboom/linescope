"""LineScope.

Author: Mavs
Description: Self-contained, escaped, accessible source profiling reports.

"""

from __future__ import annotations

import keyword
import tokenize
from copy import deepcopy
from hashlib import sha256
from html import escape
from importlib.resources import files
from io import StringIO
from pathlib import PurePath
from typing import Any

from linescope.model import (
    BackendCapabilities,
    LineStats,
    ProfileResult,
    ProfileRun,
    SourceUnit,
    SparkOperator,
)


def _key(prefix: str, identity: str) -> str:
    return f"{prefix}-{sha256(identity.encode()).hexdigest()[:20]}"


def _source_link(identity: str, line: int = 1) -> str:
    return f"#{_key('source', identity)}-L{line}"


def _time(value: int | None) -> str:
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
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _bytes(value: int | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed and value > 0 else ""
    for unit, size in (("GiB", 2**30), ("MiB", 2**20), ("KiB", 2**10)):
        if abs(value) >= size:
            return f"{sign}{value / size:,.1f} {unit}"
    return f"{sign}{value:,} B"


def _walk(run: ProfileRun) -> list[ProfileRun]:
    return [run, *[child for item in run.children for child in _walk(item)]]


def _tokens(source: str) -> dict[int, list[tuple[int, int, str]]]:
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
    for start, end in zip(ordered, ordered[1:], strict=False):
        part = escape(text[start:end])
        kind = next((kind for first, last, kind in spans if first <= start < last), None)
        if kind:
            part = f'<span class="tok-{kind}">{part}</span>'
        link = next((link for link in links if link.column <= start < link.end_column), None)
        if link:
            part = f'<a class="symbol" href="{_source_link(link.target.source_id, link.target.line)}" title="Go to {escape(link.name, quote=True)}">{part}</a>'
        result.append(part)
    return "".join(result) or " "


def _table(headers: list[str], rows: list[list[str]], *, css: str = "") -> str:
    if not rows:
        return '<p class="empty">No measurements available in this run.</p>'
    head = "".join(f'<th scope="col">{escape(header)}</th>' for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="table-scroll"><table class="{css}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _metric_value(value: Any, unit: str = "") -> str:
    if value is None:
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
    return _table(["Detail", "Value"], _metadata_rows(metadata))


def _operator_metrics(metrics: dict[str, Any]) -> str:
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


def _operator(operator: SparkOperator) -> str:
    children = "".join(_operator(child) for child in operator.children)
    if operator.name.startswith(
        ("WholeStageCodegen", "InputAdapter", "ResultQueryStage", "ShuffleQueryStage")
    ):
        return children
    metrics = _operator_metrics(operator.metrics)
    return f'<li><details><summary><span class="operator-dot"></span>{escape(operator.name)} <small>#{escape(operator.id)}</small></summary><div class="operator-detail"><pre>{escape(operator.description)}</pre>{metrics}</div></details>{"<ul>" + children + "</ul>" if children else ""}</li>'


def _source_page(
    unit: SourceUnit,
    data: dict[tuple[str, int], LineStats],
    maximum: int,
    capabilities: BackendCapabilities,
) -> str:
    page = _key("source", unit.id)
    spans = _tokens(unit.source)
    memory = capabilities.memory
    source_lines = unit.source.splitlines() or [""]
    rows = []
    for number, text in enumerate(source_lines, 1):
        stats = data.get((unit.id, number))
        duration = stats.wall_time_ns if stats else None
        hits = stats.hits if stats else (0 if capabilities.hit_counts else None)
        if hits is None and capabilities.hit_counts and duration is None:
            hits = 0
        average = None if duration is None or not hits else duration // hits
        intensity = (duration or 0) / maximum if maximum else 0
        refs = []
        if stats:
            refs.extend(
                f'<a class="badge spark" href="#{_key("spark", value)}" title="Open Spark execution">Spark #{escape(value[:8])}</a>'
                for value in stats.spark_executions
            )
            refs.extend(
                f'<a class="badge" href="#{_key("run", value)}" title="Open notebook invocation">Notebook</a>'
                for value in stats.notebook_runs
            )
        memory_cells = ""
        if memory:
            memory_cells = f'<td class="metric">{_bytes(stats.memory.delta_bytes if stats and stats.memory else None, signed=True)}</td><td class="metric">{_bytes(stats.memory.peak_bytes if stats and stats.memory else None)}</td>'
        rows.append(
            f'<tr id="{page}-L{number}" class="source-row" style="--heat:{intensity:.5f}"><td class="line-number"><a href="#{page}-L{number}" aria-label="Line {number}">{number}</a></td><td class="metric">{_time(duration)}</td><td class="metric">{"—" if hits is None else f"{hits:,}"}</td><td class="metric">{_time(average)}</td>{memory_cells}<td class="source-code"><code>{_code(text, spans.get(number, []), stats)}</code></td><td class="references">{"".join(refs)}</td></tr>'
        )
    memory_head = "<th>Driver memory Δ</th><th>Driver peak</th>" if memory else ""
    kind = "Notebook cell" if unit.kind == "notebook" else "Python source"
    return f'<section id="{page}" class="page" hidden><div class="eyebrow">{kind} · snapshotted at collection</div><h1 class="path-title">{escape(unit.path)}</h1><p class="muted">Full source · {len(source_lines)} lines · click underlined symbols to navigate</p><div class="source-scroll"><table class="source-table"><thead><tr><th scope="col">Line</th><th scope="col">{"Estimated time" if capabilities.sampled else "Python time"}</th><th scope="col">Hits</th><th scope="col">Avg / hit</th>{memory_head}<th scope="col">Source</th><th scope="col">Context</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'


def _combined_lines(runs: list[ProfileRun]) -> dict[tuple[str, int], LineStats]:
    """Combine repeated source locations across runs without inventing counts."""
    output: dict[tuple[str, int], LineStats] = {}
    for run in runs:
        for line in run.lines:
            key = (line.location.source_id, line.location.line)
            if key not in output:
                output[key] = deepcopy(line)
                continue
            merged = output[key]
            merged.wall_time_ns = _total([merged.wall_time_ns, line.wall_time_ns])
            merged.hits = (
                merged.hits + line.hits
                if merged.hits is not None and line.hits is not None
                else None
            )
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
            for name in ("calls", "spark_executions", "notebook_runs"):
                existing = getattr(merged, name)
                existing.extend(value for value in getattr(line, name) if value not in existing)
    return output


def _source_capabilities(
    result: ProfileResult, runs: list[ProfileRun]
) -> dict[str, BackendCapabilities]:
    """Keep merged child sampling and memory semantics visible per source page."""
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
        if run.source:
            ids.add(run.source.id)
        for source_id in ids:
            collected.setdefault(source_id, []).append(capability)
    return {
        source_id: BackendCapabilities(
            sampled=any(item.sampled for item in values),
            memory=any(item.memory for item in values),
            hit_counts=all(item.hit_counts for item in values),
        )
        for source_id, values in collected.items()
    }


def render_html(result: ProfileResult) -> str:
    """Render an offline report without exposing backend-specific structures.

    Parameters
    ----------
    result : ProfileResult
        Normalized measurements and exact source snapshots.

    Returns
    -------
    str
        A single HTML document containing all source, styles, and navigation.

    Notes
    -----
    Source and metadata are escaped. The document performs no network requests.
    Unknown values use an em dash; sampled estimates never invent hit counts.
    """
    runs = _walk(result.root_run)
    data = _combined_lines(runs)
    lines = list(data.values())
    capabilities = _source_capabilities(result, runs)
    mixed = any(
        run.metadata.get("child_backend", result.backend) != result.backend for run in runs
    )
    sampled = result.capabilities.sampled or any(item.sampled for item in capabilities.values())
    functions = [function for run in runs for function in run.functions]
    executions = [execution for run in runs for execution in run.spark_executions]
    measured = [line for line in lines if line.wall_time_ns is not None or line.hits]
    maximum = max((line.wall_time_ns or 0 for line in lines), default=0)
    sources = list(result.sources.values())
    source_names = {unit.id: unit.path for unit in sources}
    cards = [
        ("Elapsed wall time", _time(result.root_run.elapsed_ns)),
        ("Source snapshots", str(len(sources))),
        ("Functions", str(len(functions))),
        (
            "Observed lines" if sampled else "Executed lines",
            str(len(measured)),
        ),
        ("Spark executions", str(len(executions))),
    ]
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
        label = f"{PurePath(source_names.get(line.location.source_id, line.location.source_id)).name}:{line.location.line}"
        hot_rows.append(
            [
                f'<strong class="hot-time">{_time(line.wall_time_ns)}</strong>',
                f'<a href="{_source_link(line.location.source_id, line.location.line)}">{escape(label)}</a>',
                f"<code>{escape(source_line)}</code>",
            ]
        )
    function_rows = [
        [
            _time(item.total_time_ns),
            "—" if item.calls is None else str(item.calls),
            f'<a href="{_source_link(item.source_id, item.first_line)}">{escape(item.qualified_name)}()</a>',
        ]
        for item in sorted(functions, key=lambda item: item.total_time_ns or 0, reverse=True)
    ]
    semantics = (
        "Sampled estimates · very short lines may receive no samples. Hits and averages are unavailable."
        if result.capabilities.sampled
        else "Instrumented wall time · external work stays on its calling line. Project callees own their line time."
    )
    if mixed:
        semantics = "Mixed collection · source pages show each collector's available measurements. Sampled estimates do not provide hit counts."
    diagnostics = (
        ""
        if not result.warnings
        else '<details class="diagnostics"><summary>Collection notes</summary><ul>'
        + "".join(f"<li>{escape(note)}</li>" for note in result.warnings)
        + "</ul></details>"
    )
    pages = [
        f'<section id="overview" class="page"><div class="eyebrow">YOUR CODE, IN FOCUS</div><h1>See where the time goes.</h1><p class="intro">Follow the work from a source line to its function, notebook, or Spark plan.</p><div class="stats">{card_html}</div><div class="section-heading"><h2>Most expensive lines</h2><a href="#files">Explore all source <span aria-hidden="true">↗</span></a></div>{_table(["Time", "Location", "Source"], hot_rows, css="hot-lines")}<div class="section-heading"><h2>Functions at a glance</h2><a href="#functions">View all functions ↗</a></div>{_table(["Own line time", "Calls", "Function"], function_rows[:5])}<p class="semantics">{semantics}</p>{diagnostics}</section>'
    ]
    file_rows = [
        [
            f'<a href="{_source_link(unit.id)}">{escape(unit.path)}</a>',
            "Notebook" if unit.kind == "notebook" else "Python",
            str(len(unit.source.splitlines())),
            _time(
                _total([line.wall_time_ns for line in lines if line.location.source_id == unit.id])
            ),
        ]
        for unit in sources
    ]
    pages.append(
        f'<section id="files" class="page" hidden><div class="eyebrow">SOURCE EXPLORER</div><h1>Every line. Full context.</h1><p class="muted">Source snapshots stay readable even after your code changes.</p>{_table(["Source", "Kind", "Lines", "Measured time"], file_rows)}</section>'
    )
    pages.append(
        f'<section id="functions" class="page" hidden><div class="eyebrow">PROJECT SYMBOLS</div><h1>Functions</h1><p class="muted">Own line totals, excluding nested project callees. Select a function to open its definition.</p>{_table(["Own line time", "Calls", "Function"], function_rows)}</section>'
    )
    notebook_rows = [
        row for unit, row in zip(sources, file_rows, strict=True) if unit.kind == "notebook"
    ]
    child_rows = [
        [
            f'<a href="#{_key("run", run.id)}">{escape(run.name)}</a>',
            _time(run.metadata.get("parent_wait_time_ns", run.elapsed_ns)),
            escape(run.status),
        ]
        for run in runs[1:]
    ]
    pages.append(
        f'<section id="notebooks" class="page" hidden><div class="eyebrow">CELLS AND CHILD RUNS</div><h1>Notebooks</h1>{_table(["Snapshot", "Kind", "Lines", "Measured time"], notebook_rows)}<h2>Notebook invocations</h2>{_table(["Notebook", "Parent wait", "Status"], child_rows)}</section>'
    )
    for run in runs[1:]:
        source = (
            f'<a href="{_source_link(run.source.id)}">Open captured notebook source ↗</a>'
            if run.source
            else ""
        )
        wait_time = run.metadata.get("parent_wait_time_ns", run.elapsed_ns)
        child_note = (
            "Child profile merged. Its captured sources and measurements are included in this report."
            if run.metadata.get("collection") == "child profile merged"
            else "Child line measurements require instrumentation in that child environment."
        )
        pages.append(
            f'<section id="{_key("run", run.id)}" class="page" hidden><div class="eyebrow">NOTEBOOK INVOCATION</div><h1>{escape(run.name)}</h1><p>Parent wait: <strong>{_time(wait_time)}</strong> · {escape(run.status)}</p><p>{source}</p>{_metadata(run.metadata)}<p class="muted">{child_note}</p></section>'
        )
    spark_rows = [
        [
            f'<a href="#{_key("spark", execution.id)}">{escape(execution.name)} <small>#{escape(execution.id[:8])}</small></a>',
            _time(execution.stats.wall_time_ns),
            _time(execution.stats.executor_time_ns),
            str(len(execution.jobs)),
            escape(execution.status),
        ]
        for execution in executions
    ]
    pages.append(
        f'<section id="spark" class="page" hidden><div class="eyebrow">DISTRIBUTED EXECUTION CONTEXT</div><h1>Spark</h1><p class="muted">Python driver wall time and distributed task metrics are separate. No actions are inserted by the profiler.</p>{_table(["Execution", "Wall time", "Cumulative executor time", "Jobs", "Status"], spark_rows)}</section>'
    )
    for execution in executions:
        trigger = (
            f'<a href="{_source_link(execution.location.source_id, execution.location.line)}">Go to triggering source line ↗</a>'
            if execution.location
            else "Trigger source unavailable"
        )
        plan_views = []
        plan_buttons = []
        for label, plan in (
            ("Executed plan", execution.executed_plan),
            ("Initial plan", execution.initial_plan),
            ("Optimized logical plan", execution.optimized_plan),
            ("Parsed logical plan", execution.parsed_plan),
            ("Analyzed logical plan", execution.analyzed_plan),
        ):
            if plan is None and label != "Executed plan":
                continue
            index = len(plan_views)
            plan_buttons.append(
                f'<button type="button" class="plan-tab" data-tab="{index}" aria-pressed="{"true" if index == 0 else "false"}">{label}</button>'
            )
            plan_views.append(
                f'<pre class="plan-view" data-tab-panel="{index}"{" hidden" if index else ""}>{escape(plan or "Executed plan unavailable in this environment.")}</pre>'
            )
        operators = (
            '<ul class="operator-tree">'
            + "".join(_operator(operator) for operator in execution.operators)
            + "</ul>"
            if execution.operators
            else '<p class="muted">Operator details unavailable.</p>'
        )
        stats = execution.stats
        metrics = {
            "Wall time": _time(stats.wall_time_ns),
            "Cumulative executor time": _time(stats.executor_time_ns),
            "Rows": stats.rows,
            "Read": _bytes(stats.bytes_read),
            "Shuffle read": _bytes(stats.shuffle_read_bytes),
            "Shuffle write": _bytes(stats.shuffle_write_bytes),
            "Executor peak memory": _bytes(stats.peak_memory_bytes),
            "Spill": _bytes(stats.spill_bytes),
        }
        metric_html = _metadata(
            {key: value for key, value in metrics.items() if value is not None and value != "—"}
        )
        details = (
            "".join(
                f"<details><summary>Stage {escape(str(stage.get('id', index)))}</summary>{_metadata(stage)}</details>"
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
            f'<details class="diagnostics"><summary>Plan provenance and collection notes</summary>{_metadata(collection)}</details>'
            if collection
            else ""
        )
        pages.append(
            f'<section id="{_key("spark", execution.id)}" class="page" hidden><div class="eyebrow">SPARK EXECUTION</div><h1>{escape(execution.name)}</h1><p>{trigger}</p>{notes}{metric_html}{collection_notes}<div class="plan-tabs" role="group" aria-label="Plan views">{"".join(plan_buttons)}</div>{"".join(plan_views)}<h2>Physical operators</h2>{operators}<details class="execution-details"><summary>Stage and task details</summary>{details}</details><p class="semantics">Operator metrics are supplied by Spark. Fused and reordered plans do not imply exact per-line Spark runtimes. Executor Python workers are outside the driver profile.</p></section>'
        )
    pages.extend(
        _source_page(unit, data, maximum, capabilities.get(unit.id, result.capabilities))
        for unit in sources
    )
    source_nav = "".join(
        f'<a class="source-item" href="{_source_link(unit.id)}" title="{escape(unit.path, quote=True)}"><span class="file-icon">{"▤" if unit.kind == "notebook" else "⌘"}</span>{escape(PurePath(unit.path).name)}</a>'
        for unit in sources
    )
    css = files("linescope.render").joinpath("assets/styles.css").read_text(encoding="utf-8")
    javascript = files("linescope.render").joinpath("assets/app.js").read_text(encoding="utf-8")
    mode = "Sampling" if result.capabilities.sampled else "Tracing"
    if mixed:
        mode = "Mixed collection"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src 'none'; base-uri 'none'; form-action 'none'"><title>LineScope · Source profile</title><style>{css}</style></head>
<body><a class="skip-link" href="#main">Skip to report</a><aside class="sidebar"><a class="brand" href="#overview"><span class="brand-mark" aria-hidden="true">≋</span><span>Line<span class="brand-accent">Scope</span></span></a><div class="run-label">PROFILE WORKSPACE</div><nav aria-label="Report views"><a href="#overview" class="nav-link"><span>◈</span>Overview</a><a href="#files" class="nav-link"><span>▤</span>Files</a><a href="#functions" class="nav-link"><span>ƒ</span>Functions</a><a href="#notebooks" class="nav-link"><span>▦</span>Notebooks</a><a href="#spark" class="nav-link"><span>✧</span>Spark</a></nav><div class="source-nav"><label for="source-search">SOURCE SNAPSHOTS</label><input id="source-search" type="search" placeholder="Find a source…" autocomplete="off">{source_nav or '<p class="muted">No source captured</p>'}</div><div class="sidebar-footer"><span class="status-dot"></span> Offline report<br><small>Source stays in this file.</small></div></aside><div class="workspace"><header class="topbar"><span><span class="status-dot"></span> {escape(result.root_run.status.title())} <span class="divider">/</span> {escape(result.backend)}</span><div><span class="mode-pill">{mode}</span><button id="theme-toggle" type="button" aria-label="Switch color theme">◐</button></div></header><main id="main" tabindex="-1">{"".join(pages)}</main><footer>LineScope · Your code, in focus. <span>Python driver profiling · self-contained HTML</span></footer></div><script>{javascript}</script></body></html>"""
