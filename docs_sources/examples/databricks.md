---
notebook: examples/notebooks/databricks_example/databricks_example.ipynb
---

# Databricks notebooks

[Read the rendered Databricks notebook](notebooks/databricks_example.ipynb),
download it using the download button, or use the source example
`examples/databricks_example.py`. Install `linescope[databricks]` as a cluster
library, then restart Python if required by your Databricks runtime.

From a checkout with Just installed, run the portable notebook cells locally:

```console
just demo-databricks
```

The recipe executes `examples/notebooks/databricks_example.ipynb` and opens
`reports/databricks_example.ipynb` in JupyterLab with its inline reports.
Workspace-specific `%run` and `dbutils` calls require a Databricks workspace.
Press Ctrl+C in the terminal to stop JupyterLab.

The examples use Scalene with driver memory collection enabled on Python
3.11–3.14. Prepare the [memory allocator
environment](../user_guide/backends.md#memory) before starting the notebook
kernel.

:: example: databricks_example.py

The source export and rendered notebook show the same cell-magic and multi-cell
workloads. Run normal application cells and real actions between start and stop.
If your workflow calls a child notebook, LineScope automatically includes its
source and collects Python line measurements through a temporary workspace copy.
The parent report contains the child profile and a separate parent wait time.
Workspace export and temporary-object permissions determine what can be
collected; unavailable measurements remain unknown with diagnostic warnings.
See [Databricks](../user_guide/notebooks.md#databricks).
