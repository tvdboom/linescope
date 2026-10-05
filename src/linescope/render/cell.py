"""LineScope.

Author: Mavs
Description: Render compact notebook cell results with scoped, script-free HTML.

"""

from __future__ import annotations

from html import escape

from linescope.enums import RunStatus
from linescope.model import ProfileResult, SourceUnit
from linescope.render.html import _bytes, _count, _time

_ROW_LIMIT = 5
_STYLE = """
.linescope-cell-summary{box-sizing:border-box;width:100%;margin:8px 0;
padding:12px 16px;border:1px solid #94a3b855;border-left:3px solid #0f766e;
border-radius:6px;color:inherit;font:14px/1.5 system-ui,sans-serif}
.linescope-cell-summary header{display:flex;align-items:baseline;
justify-content:space-between;gap:12px;flex-wrap:wrap}
.linescope-cell-summary h4{margin:0;font-size:15px;color:inherit}
.linescope-cell-summary .ls-cell-meta{display:flex;flex-wrap:wrap;gap:8px 20px;
margin:6px 0 10px}
.linescope-cell-summary .ls-cell-scroll{overflow-x:auto}
.linescope-cell-summary table{width:100%;border-collapse:collapse;
font-size:13px;text-align:left;background:transparent;color:inherit}
.linescope-cell-summary th,.linescope-cell-summary td{padding:5px 8px;
border:0;border-bottom:1px solid #94a3b833;text-align:left;background:transparent;
color:inherit;vertical-align:top}
.linescope-cell-summary .ls-cell-number{text-align:right;white-space:nowrap}
.linescope-cell-summary code{font:13px/1.5 Consolas,monospace;white-space:pre-wrap;
overflow-wrap:anywhere;background:transparent;color:inherit;padding:0}
.linescope-cell-summary small{display:block;font-size:11px;opacity:.7;
overflow-wrap:anywhere}
.linescope-cell-summary p{margin:8px 0 0;font-size:12px;opacity:.8}
.linescope-cell-summary .ls-cell-failed{font-weight:600}
.linescope-cell-summary details{margin-top:8px;font-size:12px}
.linescope-cell-summary summary{cursor:pointer}
"""


def render_cell_summary(
    result: ProfileResult,
    *,
    cell: SourceUnit | None = None,
    execution_count: int | None = None,
) -> str:
    """Render a naturally sized overview of one cell's collected costs.

    Show the five busiest lines and preserve unknown metrics as em dashes.
    Escape source and diagnostics, and scope every CSS rule to this fragment
    so the surrounding notebook retains its own layout and theme.

    Parameters
    ----------
    result : [ProfileResult]
        Detached per-cell measurements, including called project code.

    cell : [SourceUnit] | None, default=None
        Executed cell snapshot, when source capture was available.

    execution_count : int | None, default=None
        IPython execution number displayed beside the cell results.

    Returns
    -------
    str
        Self-contained HTML fragment without an iframe or scripts.

    """
    run = result.root_run
    title = f"Cell {execution_count}" if execution_count is not None else "Cell results"
    failed = run.status == RunStatus.FAILED
    status = '<span class="ls-cell-failed">Failed</span>' if failed else "Completed"
    parts = [
        (
            f'<style>{_STYLE}</style><section class="linescope-cell-summary" '
            'aria-label="LineScope cell results">'
        ),
        f"<header><h4>LineScope · {escape(title)}</h4><span>{status}</span></header>",
        '<div class="ls-cell-meta">',
        f"<span>Elapsed <strong>{_time(run.elapsed_ns)}</strong></span>",
        f"<span>{len(run.lines)} measured lines</span>",
        f"<span>{escape(str(result.backend))}</span>",
    ]
    if run.spark_executions:
        parts.append(f"<span>{len(run.spark_executions)} Spark actions</span>")
    if run.children:
        parts.append(f"<span>{len(run.children)} notebook runs</span>")
    parts.append("</div>")

    lines = sorted(
        run.lines,
        key=lambda line: (line.wall_time_ns or 0, line.samples or 0, line.hits or 0),
        reverse=True,
    )
    if lines:
        columns = ["Line", "Source", "Time"]
        if result.capabilities.hit_counts:
            columns.append("Hits")
        if result.capabilities.sample_counts:
            columns.append("Samples")
        if result.capabilities.memory:
            columns.extend(("RAM change", "Python retained"))
        if result.capabilities.gpu:
            columns.append("GPU time")
        parts.append('<div class="ls-cell-scroll"><table><thead><tr>')
        parts.extend(f'<th scope="col">{column}</th>' for column in columns)
        parts.append("</tr></thead><tbody>")

        for line in lines[:_ROW_LIMIT]:
            source = result.sources.get(line.location.source_id)
            source_lines = source.source.splitlines() if source else []
            number = line.location.line
            text = source_lines[number - 1] if 0 < number <= len(source_lines) else "Unavailable"
            origin = (
                f"<small>{escape(source.path)}</small>"
                if source is not None and (cell is None or source.id != cell.id)
                else ""
            )
            values = [_time(line.wall_time_ns)]
            if result.capabilities.hit_counts:
                values.append(_count(line.hits))
            if result.capabilities.sample_counts:
                values.append(_count(line.samples))
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
                    f'<tr><td class="ls-cell-number">{number}</td>',
                    f"<td><code>{escape(text)}</code>{origin}</td>",
                    *(f'<td class="ls-cell-number">{value}</td>' for value in values),
                    "</tr>",
                )
            )
        parts.append("</tbody></table></div>")
        if len(lines) > _ROW_LIMIT:
            parts.append(
                f"<p>Showing the {_ROW_LIMIT} busiest of {len(lines)} measured lines.</p>"
            )
    else:
        parts.append("<p>No line measurements were collected for this cell.</p>")

    timing = "Sampled line estimates" if result.capabilities.sampled else "Instrumented line times"
    parts.append(f"<p>{timing}; elapsed time is measured separately. — means unavailable.</p>")
    if result.capabilities.memory:
        parts.append(
            "<p>RAM and retained Python changes are separate observations; "
            "allocations freed in this cell stay attributed to their original source line.</p>"
        )
    if result.warnings:
        parts.append("<details><summary>Collection diagnostics</summary><ul>")
        parts.extend(f"<li>{escape(warning)}</li>" for warning in result.warnings)
        parts.append("</ul></details>")
    parts.append("</section>")
    return "".join(parts)
