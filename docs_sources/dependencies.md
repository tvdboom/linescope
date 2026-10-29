# Dependencies

## Python & OS

Python 3.11–3.15 on Linux, Windows, and macOS. Scalene supports 3.11–3.14;
Tachyon requires 3.15.

## Python packages

### Required

| Package | Version |
| --- | --- |
| [Click](https://click.palletsprojects.com/) | ≥8.4.2 |
| [psutil](https://psutil.readthedocs.io/) | ≥5.9 |

### Optional

| Extra | Packages |
| --- | --- |
| `notebook` | IPython ≥8.20, ipykernel ≥6.29 |
| `scalene` | Scalene ≥2.3,<2.4 |
| `full` | Both extras |

Spark uses the environment's PySpark ≥3.5 and Databricks SDK. These are not
installed by LineScope extras.

### Development

```console
uv sync --locked
```

The default `dev` group includes tests, linting, docs, demos, pre-commit, tox,
utilities, and local Spark dependencies. The separate `gpu` group supplies
PyTorch for CUDA examples; GPU collection also needs the `scalene` extra.

See [pyproject.toml][metadata] for package constraints and
[development](development.md) for commands. Local Spark workloads need Java.

[metadata]: https://github.com/tvdboom/linescope/blob/main/pyproject.toml
