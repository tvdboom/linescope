---
notebook: examples/notebooks/databricks.ipynb
---

# Databricks notebooks

[Download the Databricks notebook](notebooks/databricks.ipynb) or use the source example
`examples/databricks_notebook.py`. Install LineScope as a cluster library or notebook dependency,
then restart Python if required by your Databricks runtime.

```python
%load_ext linescope
```

```python
from linescope import profile

session = profile.start(backend="trace", spark=True, display="end")
```

Run normal application cells and real actions. If the workflow calls a child notebook,
LineScope records the parent's wait and the child relationship. Executing this example requires
an actual workspace path that you control:

```python
result = dbutils.notebook.run("./child", 60, {"date": "2026-01-01"})
```

```python
profile.stop()
```

Parent-side observation does not measure line events in the separate child process. Use explicit
child instrumentation/correlation when you need those details. See [Databricks](../user_guide/databricks.md).
