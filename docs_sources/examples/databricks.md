---
notebook: examples/notebooks/databricks/databricks.ipynb
---

# Databricks notebooks

[Read the rendered Databricks notebook](notebooks/databricks.ipynb), download it
using the download button, or use the source example
`examples/databricks_notebook.py`. Install LineScope as a cluster library or
notebook dependency, then restart Python if required by your Databricks runtime.

:: example: databricks_notebook.py

The source export and rendered notebook show the same cell-magic and multi-cell
workloads. Run normal application cells and real actions between start and stop.
If your workflow calls a child notebook, LineScope records the parent's wait and
the child relationship. The rendered notebook explains how to add an optional
child call using an existing notebook path in your workspace.

Parent-side observation does not measure line events in the separate child
process. Use explicit child instrumentation/correlation when you need those
details. See [Databricks](../user_guide/notebooks.md#databricks).
