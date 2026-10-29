# Frequently asked questions

??? faq "Which backend should I choose?"
    Use Trace for exact execution counts, Scalene for sampling or GPU metrics,
    and Tachyon for sampling on Python 3.15. See
    [backends](user_guide/backends.md).

??? faq "Do I need to edit my script?"
    Run `linescope your_script.py` or `linescope -m your_package.your_module`.
    Use the `profile` context manager to measure only part of the program.

??? faq "Where is time inside pandas or NumPy shown?"
    Library and I/O work is attributed to the calling project line.
    Third-party source is hidden. See
    [profiling scope](user_guide/backends.md#profiling-scope).

??? faq "Does missing time mean a line never ran?"
    No. Sampling can miss short lines. A dash means unavailable; samples are
    observations, not execution counts.

??? faq "Why can a function have time but no call count?"
    Profiling may have started inside it, after its entry. Its measured time
    stays in the report; Calls and Avg / call are unavailable.

??? faq "How much overhead does profiling add?"
    Trace can noticeably slow tight loops. Memory tracking and live notebook
    reports add work too. Compare runs with the same settings and verify gains
    without the profiler. See [performance](user_guide/backends.md#performance).

??? faq "How do I save without opening a report?"
    Set `display="none"`, then call `session.save("report.html")`.
    Reports display inline in notebooks by default and in a browser elsewhere.

??? faq "Can I profile several notebook cells?"
    Start with `profile.start()`, run the cells, then call `profile.stop()`.
    See [notebook display modes](user_guide/notebooks.md#profile-several-cells).

??? faq "What does memory include?"
    Process RAM includes native buffers and profiler overhead. Retained Python
    allocations are measured separately; do not add them to RAM. Workers and
    GPU memory are outside driver RAM. See
    [memory](user_guide/backends.md#memory).

??? faq "Does profiling trigger Spark actions?"
    No. It observes existing actions. Parallel task time can exceed driver wall
    time, and executor Python lines are not profiled. See
    [Spark](user_guide/spark.md).

??? faq "Are Databricks child notebooks profiled?"
    Python children called through `dbutils.notebook.run` are collected and
    merged by default. This needs LineScope in the child environment and
    workspace permissions. See [Databricks](user_guide/notebooks.md#databricks).

??? faq "Why are some calls not clickable?"
    Dynamic dispatch, ambiguous definitions, and third-party calls may not have
    a resolvable project target. See
    [source navigation](user_guide/reports.md#source-navigation).

??? faq "Can I share a report or open it offline?"
    Reports embed their source and assets. They work without the original
    project or a server. Review captured source for private data before sharing.
