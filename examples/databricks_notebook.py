# Databricks notebook source
"""Import this source notebook into a Databricks development workspace."""

# COMMAND ----------
# MAGIC %load_ext linescope

# COMMAND ----------
# MAGIC %%profile --backend trace
# MAGIC total = sum(value * value for value in range(10_000))

# COMMAND ----------
from linescope import profile

session = profile.start(backend="trace", spark=True, display="end")

# COMMAND ----------
# Run your existing application cells here. The profiler does not create actions.
# To inspect a child notebook, use an existing path in your own workspace:
# child_result = dbutils.notebook.run("./child", 60, {"date": "2026-01-01"})

# COMMAND ----------
profile.stop()
