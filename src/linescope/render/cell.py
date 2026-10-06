"""LineScope.

Author: Mavs
Description: Render compact notebook cell results with scoped, sortable HTML.

"""

from __future__ import annotations

import ast
from html import escape
from importlib.resources import files
from io import StringIO
import re
import tokenize

from linescope.model import LineStats, ProfileResult, SourceKind, SourceLocation, SourceUnit
from linescope.render.html import (
    _bytes,
    _count,
    _heat,
    _sort_header,
    _sort_values,
    _source_name,
    _time,
)

_STYLE = """
.linescope-cell-summary{box-sizing:border-box;width:fit-content;max-width:100%;
margin:8px 0;padding:0;border:1px solid #94a3b855;border-radius:8px;
overflow:hidden;color:inherit;font:13px/1.5 system-ui,sans-serif}
.linescope-cell-summary .ls-cell-scroll{overflow-x:auto;max-width:100%;
scrollbar-width:thin}
.linescope-cell-summary .ls-cell-scroll:focus-visible{outline:2px solid #22d3ee;
outline-offset:-2px}
.linescope-cell-summary .ls-cell-table{width:max-content;min-width:0;max-width:none;
margin:0;border-collapse:collapse;table-layout:auto;text-align:left;
background:transparent;color:inherit;font:inherit}
.linescope-cell-summary .ls-cell-table th,
.linescope-cell-summary .ls-cell-table td{padding:6px 12px;
border:0;text-align:left;background:transparent;color:inherit;
vertical-align:middle;white-space:nowrap}
.linescope-cell-summary .ls-cell-table thead th{padding:8px 12px;
font-size:12px;font-weight:600;
letter-spacing:0;text-transform:none;background:#0f766e0a;
border-bottom:1px solid #94a3b855}
.linescope-cell-summary .table-sort{display:inline-flex;align-items:center;gap:8px;
margin:0;padding:0;border:0;border-radius:0;background:transparent;color:inherit;
font:inherit;text-transform:inherit;letter-spacing:inherit;white-space:nowrap;
box-shadow:none;cursor:pointer}
.linescope-cell-summary .table-sort-label{line-height:1}
.linescope-cell-summary .table-sort-icon{display:block;width:12px;height:12px;
flex-shrink:0;color:#94a3b8}
.linescope-cell-summary .sort-ascending,
.linescope-cell-summary .sort-descending{display:none}
.linescope-cell-summary th[aria-sort] .sort-neutral{display:none}
.linescope-cell-summary th[aria-sort="ascending"] .sort-ascending,
.linescope-cell-summary th[aria-sort="descending"] .sort-descending{display:block}
.linescope-cell-summary th[aria-sort] .table-sort-icon,
.linescope-cell-summary .table-sort:hover{color:#0f766e}
.linescope-cell-summary .table-sort:focus-visible{outline:2px solid #22d3ee;
outline-offset:3px}
.linescope-cell-summary .ls-cell-table tbody tr{background:color-mix(in srgb,
var(--heat-color,#dc2626) calc(var(--heat)*var(--heat-strength,45%)),transparent)}
.linescope-cell-summary .ls-cell-table tbody tr+tr td{border-top:1px solid #94a3b833}
.linescope-cell-summary .ls-cell-table tbody tr.ls-cell-gap td{
border-top:3px solid #94a3b880}
.linescope-cell-summary .ls-cell-table .ls-cell-number,
.linescope-cell-summary .ls-cell-table .ls-cell-line-number{width:1px;text-align:right;
font-variant-numeric:tabular-nums}
.linescope-cell-summary .ls-cell-table .ls-cell-line-number{padding-left:6px;
padding-right:6px}
.linescope-cell-summary .ls-cell-table .ls-cell-source{text-align:left;
padding-left:16px;padding-right:16px}
.linescope-cell-summary .ls-cell-source code{display:inline;
font:13px/1.5 Consolas,monospace;
white-space:pre;overflow-wrap:normal;word-break:normal;max-width:none;
background:transparent!important;color:inherit;padding:0!important;
border:0!important;border-radius:0!important;box-shadow:none!important}
"""


def _hidden_source_lines(source: SourceUnit) -> set[int]:
    """Identify blank, comment-only, and docstring-only rows to omit.

    Preserve executable code that shares a docstring's physical line, trailing
    comments, and string data containing comment markers. Keep unrecognized
    syntax visible when parsing fails, including incomplete notebook cells.

    Parameters
    ----------
    source : [SourceUnit]
        Original snapshot whose one-based line positions must remain intact.

    Returns
    -------
    set[int]
        One-based source lines omitted from the compact table only.

    """
    lines = source.source.splitlines() or [""]
    tokens: list[tokenize.TokenInfo] = []
    try:
        tokens.extend(tokenize.generate_tokens(StringIO(source.source).readline))
    except (tokenize.TokenError, IndentationError):
        # Failed cells can still supply complete comment tokens before the error.
        pass

    hidden = {number for number, text in enumerate(lines, 1) if not text.strip()}
    hidden.update(
        token.start[0]
        for token in tokens
        if token.type == tokenize.COMMENT
        and not lines[token.start[0] - 1][: token.start[1]].strip()
    )
    text = source.source
    if source.kind == SourceKind.NOTEBOOK:
        # Mask magic syntax while retaining positions in the original snapshot.
        text = "\n".join(re.sub(r"^(\s*)[!%]", r"\1#", line) for line in text.split("\n"))

    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return hidden

    ignored = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body or not isinstance(statement := node.body[0], ast.Expr):
            continue
        if not isinstance(statement.value, ast.Constant) or not isinstance(
            statement.value.value, str
        ):
            continue

        last = statement.end_lineno or statement.lineno
        # AST columns count UTF-8 bytes; token columns count Unicode characters.
        start = (
            statement.lineno,
            len(lines[statement.lineno - 1].encode()[: statement.col_offset].decode()),
        )
        end = (last, len(lines[last - 1].encode()[: statement.end_col_offset].decode()))
        docstring_lines = set(range(statement.lineno, last + 1))
        for token in tokens:
            if token.end[0] < statement.lineno or token.type in ignored or token.string == ";":
                continue
            if token.start >= end and token.start[0] > last:
                break
            if token.start >= start and token.end <= end:
                continue
            docstring_lines.difference_update(range(token.start[0], token.end[0] + 1))
        hidden.update(docstring_lines)

    return hidden


def _source_rows(
    result: ProfileResult,
    cell: SourceUnit | None,
) -> list[tuple[SourceUnit | None, str, LineStats]]:
    """Pair relevant source snapshots with available cell metrics.

    Show the current cell first, then sources called during this execution.
    Keep unrelated session snapshots out of the table. Create presentation-only
    rows with unknown metrics for unobserved lines, and retain measurements
    whose source text is unavailable without changing the normalized result.
    Omit blank, comment-only, and docstring-only rows from the compact table.

    Parameters
    ----------
    result : [ProfileResult]
        Detached cell measurements and cumulative source snapshots.

    cell : [SourceUnit] | None
        Current cell snapshot, including cells without any observations.

    Returns
    -------
    list[tuple[[SourceUnit] | None, str, [LineStats]]]
        Source snapshot, complete visible line text, and available measurements
        in original source order. Missing source text is labeled `Unavailable`.

    """
    sources = {cell.id: cell} if cell is not None else {}
    data = {line.location: line for line in result.root_run.lines}
    for line in result.root_run.lines:
        source = result.sources.get(line.location.source_id)
        if source is not None:
            sources.setdefault(source.id, source)

    rows = []
    for source in sources.values():
        hidden = _hidden_source_lines(source)
        for number, text in enumerate(source.source.splitlines() or [""], 1):
            location = SourceLocation(source.id, number)
            line = data.pop(location, None)
            if number not in hidden:
                rows.append((source, text, line or LineStats(location)))

    rows.extend(
        (sources.get(line.location.source_id), "Unavailable", line) for line in data.values()
    )
    return rows


def render_cell_summary(
    result: ProfileResult,
    *,
    cell: SourceUnit | None = None,
    execution_count: int | None = None,
) -> str:
    """Render a compact table of one cell's collected costs.

    Show current cell and called project source in original line order,
    omitting blank, docstring-only, and comment-only rows. Mark gaps in original
    line numbers with a thicker divider and a line-number tooltip. Keep
    unobserved code lines, preserve known zero counts, and display unknown
    metrics as em dashes. Return an empty string when no visible rows remain.
    Start with a narrow, unnamed line-number column followed by Time. Apply the
    full report's red time heatmap and keep complete source lines in
    the final column without wrapping or notebook-theme code boxes. Scroll
    horizontally when needed. Keep diagnostics in the result and scope every
    CSS rule to this fragment so the surrounding notebook retains its own
    layout and theme. Use the full report's arrows to sort metric columns in
    either direction, with unknown measurements last and ties in source order.
    The Source header sorts by original line order, ascending by default.
    A table-local click handler works when trusted notebook HTML is inserted
    or redisplayed; source text and metadata are never interpreted as
    JavaScript.

    Parameters
    ----------
    result : [ProfileResult]
        Detached per-cell measurements, including called project code.

    cell : [SourceUnit] | None, default=None
        Executed cell snapshot, when source capture was available. Other source
        paths appear in the line number's tooltip and accessible label.

    execution_count : int | None, default=None
        IPython execution number included in the table's accessible label.

    Returns
    -------
    str
        Self-contained HTML fragment with an inline sorting handler and no
        iframe or external dependencies, or an empty string when no visible
        source or measurements remain.

    """
    run = result.root_run
    rows = _source_rows(result, cell)
    if not rows:
        return ""

    label = (
        f"LineScope cell {execution_count}" if execution_count is not None else "LineScope cell"
    )
    label += f": {len(rows)} source lines"
    columns = [("Time", "time")]
    if result.capabilities.sample_counts:
        columns.append(("Samples", "samples"))
    if result.capabilities.hit_counts:
        columns.append(("Hits", "hits"))
    if result.capabilities.memory:
        columns.extend((("Mem Change", "memory"), ("Python retained", "allocation")))
    if result.capabilities.gpu:
        columns.append(("Estimated GPU time", "gpu-time"))
    columns.append(("Source", "line"))

    descriptions = {
        "Time": (
            "Sampled estimates of Python line time; red shading indicates time hotspots"
            if result.capabilities.sampled
            else "Python line time; red shading indicates time hotspots"
        ),
        "Source": "Sort by line number",
        "Mem Change": "Observed process-memory change during this cell",
        "Python retained": "Net retained Python allocation change, attributed to its source line",
    }
    handler = escape(
        files("linescope.render").joinpath("assets/cell-sort.js").read_text(encoding="utf-8"),
        quote=True,
    )
    parts = [
        (
            f'<style>{_STYLE}</style><section class="linescope-cell-summary" '
            f'data-status="{escape(str(run.status))}">'
        ),
        (
            '<div class="ls-cell-scroll" tabindex="0" role="region" '
            f'aria-label="{escape(label)}"><table class="ls-cell-table" '
            f'aria-label="{escape(label)}" onclick="{handler}"><thead><tr>'
            '<th scope="col" class="ls-cell-line-number" aria-label="Line number"></th>'
        ),
    ]
    for column, key in columns:
        css = "ls-cell-source" if column == "Source" else "ls-cell-number"
        parts.append(
            _sort_header(
                column,
                key,
                active=key == "line",
                descending=key != "line",
                title=descriptions.get(column, ""),
                css=css,
            )
        )
    parts.append("</tr></thead><tbody>")

    maximum = max((line.wall_time_ns or 0 for _, _, line in rows), default=0)
    previous: SourceLocation | None = None
    for order, (source, text, line) in enumerate(rows):
        number = line.location.line
        omitted = (
            number - previous.line - 1
            if previous is not None and previous.source_id == line.location.source_id
            else 0
        )
        gap = ' class="ls-cell-gap"' if omitted > 0 else ""
        location = ""
        if source is not None and (cell is None or source.id != cell.id):
            name = _source_name(source) if source.kind == SourceKind.NOTEBOOK else source.path
            location = f"{name}:{number}"
        if omitted > 0:
            context = (
                f"{omitted} source line{'s' if omitted != 1 else ''} omitted before line {number}"
            )
            location = f"{location}; {context}" if location else context
        origin = f' title="{escape(location)}" aria-label="{escape(location)}"' if location else ""
        raw: dict[str, str | int | None] = {
            "order": order,
            "line": number,
            "time": line.wall_time_ns,
            "samples": line.samples,
            "hits": line.hits,
            "memory": line.ram.delta_bytes if line.ram else None,
            "allocation": line.memory.delta_bytes if line.memory else None,
            "gpu-time": line.gpu.time_ns if line.gpu else None,
            "source": text,
            "gap-before": max(0, omitted),
        }
        values = []
        if result.capabilities.hit_counts:
            values.append(_count(line.hits))
        if result.capabilities.memory:
            values.extend(
                (
                    _bytes(line.ram.delta_bytes if line.ram else None, signed=True),
                    _bytes(line.memory.delta_bytes if line.memory else None, signed=True),
                )
            )
        if result.capabilities.gpu:
            values.append(_time(line.gpu.time_ns if line.gpu else None))
        parts.extend(
            (
                (
                    f'<tr{gap} style="--heat:{_heat(line.wall_time_ns, maximum):.5f}"'
                    f"{_sort_values(raw)}>"
                ),
                f'<td class="ls-cell-line-number"{origin}>{number}</td>',
                f'<td class="ls-cell-number">{_time(line.wall_time_ns)}</td>',
                *(
                    [f'<td class="ls-cell-number">{_count(line.samples)}</td>']
                    if result.capabilities.sample_counts
                    else []
                ),
                *(f'<td class="ls-cell-number">{value}</td>' for value in values),
                f'<td class="ls-cell-source"><code>{escape(text)}</code></td>',
                "</tr>",
            )
        )
        previous = line.location
    parts.append("</tbody></table></div></section>")
    return "".join(parts)
