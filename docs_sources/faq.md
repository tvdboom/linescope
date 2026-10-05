# Frequently asked questions
----------------------------

Here we try to give answers to some questions that have popped up regularly. If
you have any other questions, don't hesitate to create a new
[discussion](https://github.com/tvdboom/linescope/discussions)!

??? faq "What Python versions does LineScope support?"
    LineScope supports Python 3.11–3.15 on Linux, Windows and macOS.
    Python 3.15 offers Trace and Tachyon. See the [dependencies] page.

??? faq "Which backend should I choose?"
    Trace is the default on Python 3.11–3.15. All backends support optional
    process RAM and retained Python allocation tracking. Select Scalene for
    Python 3.11–3.14 sampling or GPU collection. Tachyon provides Python 3.15
    sampling. See
    [backends](user_guide/backends.md).

??? faq "Does a missing time mean a line never ran?"
    No. Sampling may miss short lines. Hit counts are shown only when
    measured by the backend; samples are not execution counts. Unavailable
    measurements use a dash.

??? faq "Where does the report open?"
    Reports open in a new browser tab by default, including from notebooks.
    Use `session.show(inline=True)` or `inline=True` in a notebook session to
    display inside a cell. Use `display="cell-summary"` for a compact inline
    overview after each cell while debugging. Use `display="none"` to suppress
    automatic display and `session.save("report.html")` to keep a file
    explicitly.

??? faq "Can I collect GPU metrics?"
    Yes. Enable `gpu=True` or `--gpu` with Scalene on a supported device.
    See [GPU](user_guide/gpu.md). Trace and Tachyon do not provide GPU metrics.

??? faq "Why can a Spark task total exceed elapsed time?"
    Tasks execute concurrently. Summed executor/task time is cumulative
    work, while wall time is elapsed time observed by the driver. These
    measurements are labeled separately.

??? faq "Does it profile Python UDFs on Spark executors?"
    LineScope profiles driver Python and available Spark execution context.
    Separate executor Python processes require their own instrumentation
    and correlation; driver measurements do not include their line profiles.

??? faq "Does a child Databricks notebook get deep profiling automatically?"
    The parent records its wait and child relationship. Separate child line
    measurements require child instrumentation and explicit transport/merging;
    the parent cannot trace a separate job.

??? faq "Is a Spark cluster required for tests?"
    No. Unit tests use controlled fakes. Optional integration tests start
    local Spark on one machine with Java and generated data. Databricks
    runtime validation uses a workspace.

??? faq "Can I open reports offline?"
    Yes. HTML reports contain their own assets and source snapshots.
    Review captured source before sharing a report.
