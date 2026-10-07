# Optional command runner: uv tool install rust-just
# Run `just sync` to install everything; other recipes sync without pruning.
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

default:
    @just --list

# Install every extra and dependency group from the lockfile.
sync:
    uv sync --locked --all-extras --all-groups

build:
    uv build

test *args:
    uv run pytest {{args}}

lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check

# Run every pre-commit hook against all tracked files.
pre-commit:
    uv run pre-commit run --all-files

format:
    uv run ruff check --fix .
    uv run ruff format .

tox *args:
    uv run tox {{args}}

docs dev_addr="127.0.0.1:8001":
    uv run python -m mkdocs serve --dev-addr {{dev_addr}}

docs-build:
    uv run python -m mkdocs build --strict

spark-test:
    uv run tox -e spark

# Execute all example notebooks with nbmake; requires Java on PATH.
notebook-test:
    uv run tox -e notebooks

alias demo := demo-script

# Run the documented script example and open its HTML report.
demo-script:
    uv run python examples/script_example.py

# Use the checkout's kernel and keep executed notebooks in reports/.
_demo-notebook name extra_args="":
    uv run --group demo --extra notebook {{extra_args}} python -m ipykernel install --sys-prefix --name python3
    uv run --group demo --extra notebook {{extra_args}} python -m nbconvert --to notebook --execute --ExecutePreprocessor.timeout=600 --output-dir reports --output {{name}} examples/notebooks/{{name}}.ipynb
    uv run --group demo --extra notebook {{extra_args}} python -m nbformat.sign reports/{{name}}.ipynb
    uv run --group demo --extra notebook {{extra_args}} python -m jupyterlab --ServerApp.root_dir=. reports/{{name}}.ipynb

# Execute the notebook example and open its outputs in JupyterLab (Ctrl+C to stop).
demo-notebook: (_demo-notebook "notebook_example")

# Execute the NVIDIA CUDA notebook and open its outputs; requires a CUDA GPU.
demo-gpu: (_demo-notebook "gpu_example" "--group gpu")

# Execute the Spark notebook and open its outputs; requires Java on PATH.
demo-spark-notebook: (_demo-notebook "spark_example" "--group spark")

# Run the documented local Spark example and open its report; requires Java on PATH.
demo-spark:
    uv run --group spark python examples/spark_example.py
    uv run python -c "from pathlib import Path; import webbrowser; webbrowser.open(Path('spark.html').resolve().as_uri())"
