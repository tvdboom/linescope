"""Helpers with source definitions in a second module."""

from time import sleep


def prepare(size: int) -> list[int]:
    """Generate deterministic input after a small I/O-like wait."""
    sleep(0.01)
    return list(range(size))


def calculate(values: list[int]) -> int:
    """Compute a simple aggregate."""
    return sum(value * value for value in values)
