---
notebook: examples/notebooks/notebook_example/notebook_example.ipynb
---

# Notebook session

[Read the rendered notebook](notebooks/notebook_example.ipynb), or download it
using the download button. It contains a cell-magic example and a multi-cell
session, with no external data or network dependency.

From a repository checkout with Just installed, run:

```console
just sync
just demo-notebook
```

`just sync` installs all extras and dependency groups. The demo recipe runs the
quickstart with the checkout's Python kernel and opens
`reports/notebook_example.ipynb` in JupyterLab with the inline reports already
available. The original example stays unexecuted. The final cell also saves
`notebook.html` beside the original notebook. Press Ctrl+C in the terminal to
stop JupyterLab.

Run `examples/notebooks/notebook_example.ipynb` with LineScope and IPython
installed. The rendered page shows the complete notebook from the examples
folder, including every cell.

First load the extension and profile one cell with
`%%profile --backend scalene --memory --inline`. Then start a session, run the
two workload cells, and stop it. The full report appears at the final stop
rather than after every intermediate cell. The last code cell saves the report
to `notebook.html` so you can reopen it later.

The final section demonstrates `display="cell-summary"` with Trace. Each
workload cell gets a compact inline overview of its own results. Stop collection
without an automatic full report, then use `session.show()` or `session.save()`
when you want to inspect the cumulative session.

Use Python 3.11–3.14 for these Scalene demos. The
[shared memory collector](../user_guide/backends.md#memory) needs no allocator
preload or kernel restart.
