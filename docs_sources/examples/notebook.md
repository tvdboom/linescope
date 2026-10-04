---
notebook: examples/notebooks/quickstart/quickstart.ipynb
---

# Notebook session

[Read the rendered notebook](notebooks/quickstart.ipynb), or download it using
the download button. It contains a cell-magic example and a multi-cell session,
with no external data or network dependency.

From a repository checkout with Just installed, run:

```console
just demo-notebook
```

The recipe installs the notebook extra and the optional `demo` dependency group,
runs the quickstart with the checkout's Python kernel, and opens
`reports/quickstart.ipynb` in JupyterLab with the inline reports already
available. The original example stays unexecuted. The final cell also saves
`notebook.html` beside the original notebook. Press Ctrl+C in the terminal to
stop JupyterLab.

Run `examples/notebooks/quickstart.ipynb` in an environment with LineScope and
IPython installed. The rendered page shows the complete notebook from the
examples folder, including every cell.

First load the extension and profile one cell with
`%%profile --backend trace --inline`. Then start a session, run the two workload
cells, and stop it. The full report appears at the final stop rather than after
every intermediate cell. The last code cell saves that same report to
`notebook.html` so you can reopen it later.
