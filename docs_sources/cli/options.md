# CLI options

```console
linescope --help
python -m linescope --help
```

| Option | Purpose |
| --- | --- |
| `--backend NAME` | Choose `scalene` (default) or `trace` |
| `--include RULE` | Include a project package/path; repeat for more rules |
| `--exclude RULE` | Exclude project code; repeat for more rules |
| `--memory` | Request optional Python memory metrics |
| `--spark` | Enable Spark observation |
| `--no-spark` | Disable Spark observation |
| `--no-notebooks` | Disable notebook integration |
| `--output PATH` | Save the self-contained HTML report |
| `--display none/end` | Save silently (CLI default) or display at completion |
| `-m MODULE` | Run a Python module instead of a script |

```console
linescope --backend trace --include src --exclude tests --output run.html job.py
linescope --backend scalene --memory job.py
linescope --backend trace --spark spark_job.py
```

Backend errors describe missing optional dependencies or unsupported runtime capabilities. Future
attach-to-PID/Tachyon commands are not part of the initial command-line interface. Run `--help`
for the installed version's complete option list.
