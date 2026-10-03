# Notebook integrations

Load the extension with `%load_ext linescope`. It registers `%%profile` and cooperates with the
active IPython shell to capture cell source. The public start/stop controller supports sessions
spanning several cells without generating a report after each cell by default.

## Source units

Notebook source uses `SourceUnit(kind="notebook")`. A source identifier includes the notebook
identity and cell identity, and the captured text is stored directly in `ProfileResult.sources`.
Reports do not depend on a live notebook server to display those snapshots.

## Child correlation

`linescope.notebooks.correlation` provides correlation parameters and merge support for explicitly
instrumented child runs. Parent/child identifiers belong in the normalized run model. Transport
between separate Databricks jobs is left to deployment-specific bootstrap; credentials and
unredacted job parameters should not be put into correlation payloads.

`linescope.notebooks.databricks` isolates workspace metadata and `dbutils.notebook.run`
observation, making them testable without a Databricks account.

## ChildContext

:: linescope.notebooks.correlation:ChildContext
    :: signature
    :: head
    :: table:
        - parameters
    :: methods

## merge_child

:: linescope.notebooks.correlation:merge_child
    :: signature
    :: head
    :: table:
        - parameters
        - returns
        - raises

## NotebookIntegration

:: linescope.notebooks:NotebookIntegration
    :: signature
    :: head
    :: table:
        - parameters
        - returns
    :: methods
