"""Profile a small deterministic pipeline without external dependencies."""

from pathlib import Path
from time import sleep

from linescope import profile


def load_values():
    """Simulate an external I/O operation."""
    sleep(0.02)
    return list(range(20_000))


def normalize(values):
    """Scale values with a locally resolvable function."""
    scale = max(values) or 1
    return [value / scale for value in values]


def main():
    """Write a report and print its path."""
    with profile(backend="trace", display="none", root=str(Path(__file__).parent)) as session:
        values = load_values()
        normalized = normalize(values)
        average = sum(normalized) / len(normalized)
    report = session.save("linescope.html")
    print(f"Average: {average:.3f}; report: {report}")


if __name__ == "__main__":
    main()
