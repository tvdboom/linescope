"""Run with linescope --backend trace -m examples.sample_package."""

from .helpers import calculate, prepare


def main():
    """Execute a nested cross-module call."""
    print(calculate(prepare(20_000)))


if __name__ == "__main__":
    main()
