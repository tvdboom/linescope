# Reading reports

A report is a small source browser. Select Overview to find hot lines, Files to inspect the
surrounding code, Functions to jump to definitions, and the notebook/Spark views for contextual
information. Source snapshots remain available even after the original files change.

![Overview of a real trace run, with its most expensive lines](../img/report.jpg)

This example comes from the repository's `examples/profile_script.py`. Times depend on the
machine, backend, and workload; the screenshot illustrates the report rather than a benchmark.

## Line columns

| Column | Interpretation |
| --- | --- |
| Time | Time attributed to the source line by the active backend |
| Hits | Recorded executions when the backend supports them |
| Average | Time divided by hits, only when both are available |
| Memory | Python-driver delta/peak values, when supported and requested |
| Spark | Separate navigation references to observed executions |

Heat intensity makes expensive lines easier to find. Unexecuted source stays visible. A dash
means unavailable or unmeasured; sampled zeroes do not prove that a line took no time.

## Links

![Full source with timing, hits, heatmap rows, and function links](../img/source.jpg)

Function and class links target their definitions. Each call is linked separately, so
`outer(inner(value))` can have two links. An unresolved or third-party call remains unlinked.
Spark badges are separate from symbol links. Notebook paths can point to captured notebook
source or child-run metadata.

The report uses hash navigation, selected-line highlighting, and browser back/forward controls.
It does not fetch source from a server. Before sharing, review the embedded source, paths, and
integration metadata; source snapshots may contain private application details.
