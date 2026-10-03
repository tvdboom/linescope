# Databricks

Install LineScope into the cluster or notebook environment, then load the IPython extension
and use the cell magic or explicit start/stop API. Keep the Databricks-provided PySpark version;
the LineScope `spark` extra is intended for environments that need their own local installation.

## Workspace source

Notebook adapters preserve a workspace path when the runtime exposes it. Otherwise, a stable
virtual notebook identifier is used. Source is captured from executed cells instead of assuming
that a workspace notebook is a normal Python file.

## Inline `%run`

Databricks `%run ./common` executes another notebook within the current notebook environment.
LineScope records resolvable notebook references and captured source when the environment exposes
it. Later calls can link to definitions from that source. Paths with no accessible source remain
references; LineScope does not invent the child notebook's code.
Capture uses source exposed by the running shell; LineScope does not fetch remote notebook source.

## `dbutils.notebook.run`

This API creates a separate child execution. The parent can observe its path, calling location,
elapsed wait, success/failure, and safe correlation metadata. It cannot directly trace line
events in another process. Child runs appear under the parent run; sensitive arguments are
redacted at the integration boundary. All argument values and returned content are omitted;
the return type/length can be retained without storing the result itself.

The [correlation API](../api/notebooks.md) supports explicitly merging a separately collected
child result. Automatic remote installation/bootstrap and transport are deployment concerns in
this release. Parent wait time is never presented as child line-level profiling.

Use the [Databricks example](../examples/databricks.md) and the manual acceptance checklist in
[testing](../development/testing.md) to validate your runtime's supported hooks.
