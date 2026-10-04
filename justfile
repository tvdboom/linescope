# Optional command runner: uv tool install rust-just
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

default:
    @just --list

sync:
    uv sync --locked

build:
    uv build

test *args:
    uv run --no-sync pytest {{args}}

lint:
    uv run --no-sync ruff check .
    uv run --no-sync ruff format --check .
    uv run --no-sync ty check

# Run every pre-commit hook against all tracked files.
pre-commit:
    uv run pre-commit run --all-files

format:
    uv run --no-sync ruff check --fix .
    uv run --no-sync ruff format .

tox *args:
    uv run --no-sync tox {{args}}

docs dev_addr="127.0.0.1:8001":
    uv run --no-sync python -m mkdocs serve --dev-addr {{dev_addr}}

docs-build:
    uv run --no-sync python -m mkdocs build --strict

spark-test:
    uv run --no-sync tox -e spark

# Execute all example notebooks with nbmake; requires Java on PATH.
notebook-test:
    uv run --no-sync tox -e notebooks

alias demo := demo-script

# Run the documented script example and open its HTML report.
demo-script:
    uv run --locked python examples/script.py

# Execute the documented quickstart, then open its outputs in JupyterLab (Ctrl+C to stop).
demo-notebook:
    uv run --locked --group demo --extra notebook python -m ipykernel install --sys-prefix --name python3
    uv run --locked --group demo --extra notebook python -m nbconvert --to notebook --execute --ExecutePreprocessor.timeout=120 --output-dir reports --output quickstart examples/notebooks/quickstart.ipynb
    uv run --locked --group demo --extra notebook python -m nbformat.sign reports/quickstart.ipynb
    uv run --locked --group demo --extra notebook python -m jupyterlab --ServerApp.root_dir=. reports/quickstart.ipynb

# Run the documented local[2] Spark example and open its report; requires Java on PATH.
demo-spark:
    uv run --locked --extra spark python examples/local_spark.py
    uv run --no-sync python -c "from pathlib import Path; import webbrowser; webbrowser.open(Path('spark.html').resolve().as_uri())"
