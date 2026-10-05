# Reports

A report is a small source browser. Select Overview to find hot lines, Files to
inspect the surrounding code, Functions to jump to definitions, and the
notebook/Spark views for contextual information. Source snapshots remain
available even after the original files change.

The report fits the browser window. When viewing a source file, its title and
line count stay visible while the code table scrolls within the remaining
space. Column headings stay visible as you scroll, and long source lines scroll
horizontally inside the same table.

Use the buttons above the source table to order lines by Line number (the
default) or Time (highest first). When memory collection is enabled for that
source, Mem Growth orders by accumulated process-memory change, highest first.
Unknown values appear last, and equal values keep line-number order. All source
lines and navigation links remain available in every order.

The Heatmap by control sits to the left of line ordering. Select Time (the
default) or, when memory collection is enabled, Mem Growth to color positive
process-memory growth. Heatmap selection and line ordering are independent.

Source tables show Hits and Avg / hit when execution counts are available,
and Samples when observation counts are available. Sampling backends omit
the hit columns; tracing omits the sample column. Each source keeps its own
columns when child runs use different collectors. The Context column appears
only when that source has Spark execution or child-notebook invocation links.

The Functions table shows Function, Lines, Measured time, then Samples for
sampling reports or Calls for tracing reports, matching the Files page order.
Measured time adds up time spent on the function's own lines, excluding time
in other project functions it calls. Sampling reports estimate this time.
Reports containing sampled child runs use Samples; functions without sampling
counts show a dash.

Lines counts source lines from the `def` or `async def` statement through the
last statement in the body, including blank lines, comments, and nested
definitions. Decorators and trailing comments are excluded. The count includes
lines that were never executed or sampled; unavailable counts show a dash.

The Notebooks view appears when the report contains notebook snapshots or child
notebook invocations. The Spark view and its overview count appear only when
Spark executions were captured, including executions in child notebooks.

![Report overview with the most expensive lines](../img/report.jpg)

This example comes from the repository's `examples/script_example.py`. Times
depend on the machine, backend, and workload; the screenshot illustrates the
report rather than a benchmark.

## The main views

| View | Question it answers |
| --- | --- |
| Overview | Where should I start investigating? |
| Files | Which lines in this source file consumed time? |
| Functions | Where is it defined, and what time was attributed to it? |
| Memory | When did process RAM grow, and which line was active? |
| Notebooks | Which captured cells and child notebooks belong to this run? |
| Spark | How does data flow, and which steps cost time or memory? |

## Report header

The header separates the run outcome, the backend used, and its collection
method. Select any of these labeled values to open the relevant user-guide
section in a new tab. These help links require an internet connection; the
report itself works offline.

### Run status

`Success` means the run has no recorded failure. `Failed` means LineScope
recorded an error in the profiled run. A report can still contain useful
measurements from before the failure. Notebook entries marked `Reference`
represent inline execution that belongs to their parent run.

### Backend collector

The backend names the collector that produced the measurements: `trace`,
`scalene`, `tachyon`, or a registered custom backend. Select its name to read
about that collector's capabilities and limitations in [Backends](backends.md).

### Collection method

`Tracing` records Python execution events to measure line wall intervals and,
when supported, execution counts. `Sampling` estimates time from periodic
observations and cannot provide exact hit counts. `Mixed collection` means
the report combines runs whose collection capabilities differ.

This label describes how the measurements were collected. It does not indicate
that profiling is still running. See
[backend performance](backends.md#performance) for the effects of
instrumentation on timings.

## Profiling scope

The main run's backend determines which Python threads are observed. Built-in
backends do not collect separate worker processes automatically. Child runs can
use their own backend.
See [backend scope](backends.md#profiling-scope) for thread coverage.

Elapsed wall time includes waits within the profiling session. It is not total
CPU time across every worker. With Spark, the report separates driver action
waiting from available cumulative executor/task metrics; these can overlap and
must not be added together. Workers have no Python source-line or allocation
measurements in the driver profile. See [Spark worker
scope](spark.md#worker-scope-and-overall-time) for an example.

## Line columns

| Column | Interpretation |
| --- | --- |
| Time | Time attributed to the source line by the active backend |
| Hits | Recorded executions when the backend supports them |
| Average | Time divided by hits, only when both are available |
| Samples | Observed project thread frames, shown for sampling sources |
| Mem Change | Accumulated process-memory change during this line's intervals |
| Peak Mem | Highest observed process memory during this line |
| Estimated GPU time | Sampled device work, separate from driver wall time |
| GPU peak memory | Highest sampled device-memory value for the line |
| Spark | Separate navigation references to observed executions |

Heat intensity uses a logarithmic scale relative to the report's largest
visible value for the selected metric. This keeps smaller hotspots visible
when one line dominates, using the same scale across source files. Time heat
leaves unmeasured and zero-time lines uncolored. Mem Growth heat colors only
positive accumulated changes; decreases, zeroes, and unavailable readings have
no heat. Switching heatmaps preserves the measurements and current line order.
A dash means unavailable or unmeasured; sampled zeroes do not prove that a line
took no time. Source backgrounds, syntax colors, and links follow the report's
light or dark theme, including the system preference.

The [backend](backends.md) determines which columns can contain measurements.
With `memory=True`, source tables show Mem Change and Peak Mem for the profiled
Python process. Retained Python allocation changes remain in the collected
profile data; the source table displays only process-memory measurements.
Byte measurements use decimal units: 1 KB is 1,000 bytes, 1 MB is 1,000,000
bytes, and 1 GB is 1,000,000,000 bytes.
With `memory=True`, the Memory view starts with badges for the highest observed
process RAM and the largest accumulated change on one source line. These
summaries link to the corresponding source and precede the RAM timeline. Move
the inspection slider to a reading or select a chart point to open its source
line. Below the chart, expand the retained readings or inspect the growth
ranking to identify lines that accumulate RAM across loop iterations. Separate
child processes keep their own badges and timelines. Expand About these
measurements below the results for the measurement definitions. See
[memory](backends.md#memory) for the scope, overhead, and observed-peak
limitations.

Sampling reports include Samples in the hot-line, source, Functions, Files, and
Notebooks overviews, plus a total sample count and the main run's target samples
per second. Function counts sum the observations of their own lines, excluding
nested definitions. File and notebook counts sum their captured line counts.
Child runs retain their own sampling settings in their invocation details.

Samples are observations, not execution hits or function calls. Scalene can
observe several project threads in one sampling pass; each accepted thread
frame contributes one sample to its nearest project line. Its time estimates
may redistribute cost across nearby lines, so counts cannot be reconstructed
from time. An unsampled line has zero observations when the collector supplies
sample counts; unavailable counts remain a dash. Configure the target rate as
described in [Sampling rate](backends.md#sampling-rate).

## Source snapshots

Project discovery uses the configured root, nearby project metadata, and
explicit include/exclude rules. Installed third-party libraries, interpreter
internals, and unrelated files remain outside the source browser. Their work can
still contribute to the calling project's measured time.

Source is stored with the profile result, including notebook cells. Editing or
deleting the original file later does not change the saved report. A source unit
has an identifier, original path, text, and kind (`python` or `notebook`).

Python source titles and file lists show only the filename. Files with the same
name keep distinct navigation targets through their original source identities.

See [configuration](configuration.md) for scope controls.

## Source navigation

![Source timing, heatmap rows, and function links](../img/source.jpg)

Function and class links target their definitions. Each call is linked
separately, so `outer(inner(value))` can have two links. An unresolved or
third-party call remains unlinked. Spark badges are separate from symbol links.
Notebook paths can point to captured notebook source or child-run metadata.

Linked symbols are colored and underlined. Selecting a link places its
definition immediately below the source table's column headings, including
definitions near the end of a file.

Static analysis builds an index of project functions, classes, methods, imports,
and call sites. The renderer links individual symbol spans. Multiple calls on
one line remain separate targets. Known local class instances can resolve
methods; dynamic dispatch, runtime imports, and ambiguous names may remain
unlinked. An absent link is preferable to a misleading destination.

Includes and excludes are scope controls, not a mechanism for obtaining private
code from inaccessible locations. Notebook references use captured or explicitly
registered source.

The report uses hash navigation, line links, and browser back/forward controls.
It does not fetch source from a server.

## Saving and sharing

Call `session.save("report.html")` to write a self-contained HTML report. The
file embeds its source and assets and works offline without the original project
files or a running server.

Review the embedded source, paths, and integration metadata before sharing
outside your project, especially notebook cells containing inline credentials or
private paths. Escaping prevents source text from becoming executable HTML; it
does not remove sensitive source.
