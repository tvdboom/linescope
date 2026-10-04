# Databricks notebook source
"""LineScope.

Author: Mavs
Description: Import this source notebook into a Databricks development
workspace.

"""

# COMMAND ----------
# MAGIC %load_ext linescope

# COMMAND ----------
# MAGIC %%profile --backend scalene --memory --inline
# MAGIC total = sum(value * value for value in range(10_000))

# COMMAND ----------
from linescope import profile

session = profile.start(backend="scalene", memory=True, spark=True, display="end", inline=True)

# COMMAND ----------
values = list(range(10_000))
total = sum(values)

# COMMAND ----------
# Run your existing application cells here. The profiler does not create actions.
# To inspect a child notebook, use an existing path in your own workspace:
# Call it with dbutils.notebook.run as part of your normal workload.

# COMMAND ----------
profile.stop()
