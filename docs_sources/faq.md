# Frequently asked questions
----------------------------

Here we try to give answers to some questions that have popped up regularly. If
you have any other questions, don't hesitate to create a new
[discussion](https://github.com/tvdboom/linescope/discussions)!

??? faq "Which backend should I choose?"
    Trace is the default and records exact execution counts and line timings.
    Choose Scalene for CPU sampling or supported GPU measurements, or Tachyon
    for sampling on Python 3.15. All three support optional process RAM and
    retained Python allocation tracking. See
    [backends](user_guide/backends.md) for their scope and tradeoffs.

??? faq "Do I need to change my script to profile it?"
    No. Run an existing script or importable module through the CLI:

    ```console
    linescope --output report.html your_script.py
    linescope --output report.html -m your_package.your_module
    ```

    Put LineScope options before the script path; arguments after the path go
    to your script. To profile only part of a workload, use the `profile`
    context manager. See [getting started](getting_started.md#usage).

??? faq "Where is time spent inside pandas, NumPy, or other libraries shown?"
    Library work is attributed to the nearest relevant project line, such as
    the call that enters the library. Reports keep your source in view and
    hide third-party internals. This includes time waiting for I/O or native
    code; line time is not a separate CPU counter for each dependency. See
    [profiling scope](user_guide/backends.md#profiling-scope).

??? faq "Does a missing time mean a line never ran?"
    No. Sampling may miss short lines. Hit counts are shown only when
    measured by the backend; samples are not execution counts. Unavailable
    measurements use a dash.

??? faq "Will profiling make my program slower?"
    Profiling adds overhead. Trace observes every line event and can affect
    tight Python loops noticeably. Sampling avoids recording every event,
    but can miss brief operations. Memory collection and live notebook reports
    add work too. Compare results with the same backend and inputs, and check
    improvements with an unprofiled run. See
    [performance](user_guide/backends.md#performance).

??? faq "Where does the report open?"
    Reports display inside notebook cells automatically and open a browser
    elsewhere. Choose the destination with `session.show(inline=True)` or
    `session.show(inline=False)`. Use `display="cell-summary"` for a compact
    inline overview after each cell while debugging. Use `display="none"` to
    suppress automatic display and `session.save("report.html")` to keep a
    file explicitly.

??? faq "Can I profile several notebook cells in one report?"
    Yes. Call `profile.start()` before the cells you want to measure and
    `profile.stop()` after them. The default produces one cumulative report
    at stop. Use `display="cell"` for live cumulative reports or
    `display="cell-summary"` for each cell's own compact overview. See
    [notebooks](user_guide/notebooks.md#profile-several-cells).

??? faq "What do the memory measurements include?"
    Enable `memory=True` or `--memory` to collect process RAM and net retained
    Python allocation changes. RAM includes native library buffers and
    profiler overhead; allocation tracking covers traced Python allocations.
    These values measure different things and should not be added together.
    Separate worker processes, Spark executors, and GPU memory are outside
    driver RAM measurements. See [memory](user_guide/backends.md#memory).

??? faq "Can I collect GPU metrics?"
    Yes. Enable `gpu=True` or `--gpu` with Scalene on a supported device.
    See [GPU](user_guide/gpu.md). Trace and Tachyon do not provide GPU metrics.
    Trace warns and continues Python profiling if GPU collection is requested;
    Tachyon rejects the request.

??? faq "Does LineScope trigger extra Spark actions?"
    No. It observes the actions your workload already performs and never
    forces `count`, `collect`, or another action to measure a lazy
    transformation. It does not create a Spark session. See
    [lazy execution](user_guide/spark.md#lazy-execution).

??? faq "Why can a Spark task total exceed elapsed time?"
    Tasks execute concurrently. Summed executor/task time is cumulative
    work, while wall time is elapsed time observed by the driver. These
    measurements are labeled separately.

??? faq "Does it profile Python UDFs on Spark executors?"
    LineScope profiles driver Python and available Spark execution context.
    Separate executor Python processes require their own instrumentation
    and correlation; driver measurements do not include their line profiles.

??? faq "Does a child Databricks notebook get deep profiling automatically?"
    Yes, by default for Python notebooks called with `dbutils.notebook.run`
    while profiling. LineScope collects child source and line measurements
    separately, then merges them into the parent report. This needs LineScope
    in the child environment and workspace export/create/delete permissions.
    Use `child_notebooks=False` for parent-side observation only. Collection
    limitations appear as warnings; see
    [Databricks](user_guide/notebooks.md#dbutilsnotebookrun).

??? faq "Why are some function calls not clickable in the report?"
    LineScope links a call only when its project definition can be resolved
    conservatively. Dynamic dispatch, runtime imports, ambiguous names, and
    third-party calls can remain unlinked. The line's available measurements
    remain visible. See
    [source navigation](user_guide/reports.md#source-navigation).

??? faq "Can I open reports offline?"
    Yes. HTML reports contain their own assets and source snapshots, so they
    work without a server or the original project files. Editing or deleting
    the original source does not change a saved report. Review captured source
    before sharing; see
    [saving and sharing](user_guide/reports.md#saving-and-sharing).
