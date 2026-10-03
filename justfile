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

demo:
    uv run --no-sync python examples/profile_script.py
