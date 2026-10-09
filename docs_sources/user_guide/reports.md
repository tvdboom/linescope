# Reports

Start with **Overview** to find expensive lines, then follow a source link to
inspect the surrounding code. Reports store source snapshots, so you can still
read the profiled version after the original files change.

![Report overview with the most expensive lines](../img/report.jpg)

## The main views

| View | Use it to |
| --- | --- |
| Overview | Find the lines worth investigating first |
| Files | Browse files, notebooks, and child notebook invocations |
| Functions | Compare function time and jump to definitions |
| Memory | Follow process RAM growth and inspect active source lines |
| GPU | Compare sampled device work and memory with driver time |
| Spark | Inspect actions, query plans, and distributed metrics |

Memory and GPU views depend on the enabled collectors. Spark appears when
executions have been captured, including those in child notebooks.

### Sorting and heatmaps

Select a column heading to sort; select it again to reverse the order. Unknown
measurements stay last. In source tables, select **Source** to restore line
order. Notebook sorting stays within each cell.

Use **Heatmap by** to highlight **Time** or, with memory collection enabled,
positive **Mem Growth**. Select **None** to turn it off. Sorting and heatmaps
work independently.

## Report header

The header shows the run status, backend, and collection method. Select a value
to open its guide page; these help links need an internet connection.

### Run status

`Success` means no failure was recorded. `Failed` reports retain measurements
collected before the error. Notebook entries marked `Reference` represent
inline execution that belongs to their parent run.

### Backend collector

The backend identifies the collector: `trace`, `scalene`, `tachyon`, or a
[custom backend](backends.md#custom-backends).

### Collection method

`Tracing` records execution events. `Sampling` estimates time from periodic
observations. `Mixed collection` combines runs with different collection
capabilities. See [Backends](backends.md) for their tradeoffs.

## Profiling scope

Elapsed wall time includes waits during the session. It does not total CPU time
across workers. Thread coverage depends on the
[backend](backends.md#profiling-scope); separate worker processes are not
profiled automatically.

Spark reports keep driver waits separate from cumulative task metrics, which
can overlap. See [Spark timing](spark.md#worker-scope-and-overall-time).

## Line columns

| Column | Meaning |
| --- | --- |
| Time | Time attributed to the line; estimated by sampling |
| Samples | Observations of the line, rather than execution counts |
| Hits | Recorded executions, when supported |
| Avg / hit | Time divided by hits |
| Mem Change | Accumulated process-memory change during the line's intervals |
| Peak Mem | Highest observed process memory during the line |
| GPU time | Estimated device work, separate from driver wall time |
| GPU peak memory | Highest sampled device-memory value for the line |
| Context | Links to Spark executions or child notebook invocations |
| Source | Captured source text |

Only supported columns appear. A dash means unavailable or unmeasured; zero
samples do not prove that a line never ran. Sampling reports cannot provide
hit counts or per-hit averages. The reported sampling rate is the main run's
observed samples divided by its elapsed time.

Function time sums the function's own lines, excluding other project functions
it calls. **Lines** counts the definition and body, including comments, blank
lines, and nested definitions, whether they executed or not.

### Memory

With `memory=True`, source tables show process RAM measurements. The **Memory**
view shows the observed peak, largest accumulated line change, timeline, and
growth ranking. Use the ranking to find lines that retain RAM over repeated
iterations.

Hover over the timeline to inspect a reading and follow its source link when
available. You can also focus the plot, use arrow keys to move between readings,
and press Enter to open linked source. A linked line was active at the reading;
it does not necessarily own the allocations.

Retained Python allocation changes are available in the profile data and
compact cell summaries. They measure something different from process RAM.
Child processes have separate timelines. See
[memory collection](backends.md#memory) for scope and peak limitations.

## Source snapshots

The report includes project files and captured notebook cells, selected through
[configuration](configuration.md). Third-party internals stay outside the source
browser, but their cost can contribute to the calling project line.

Each notebook appears once in **Files**, with cells in capture order and line
numbers restarting in each cell. Source links target the exact cell and line.
If a cell changes during collection, its revisions remain available. Cells
outside collection are not reconstructed.

## Source navigation

![Source timing, heatmap rows, and function links](../img/source.jpg)

Select an underlined function or class to jump to its definition. Calls on the
same line have separate links. Dynamic dispatch, ambiguous definitions, and
third-party calls may remain unlinked.

Spark and child notebook links open their captured context. Use browser back
and forward to retrace navigation.

## Saving and sharing

Call `session.save("report.html")` to write a self-contained report. It embeds
source and assets and works offline without the original project or a server.

Review source snapshots, paths, and integration metadata before sharing.
Notebook cells can contain credentials or private data; HTML escaping does not
remove them.
