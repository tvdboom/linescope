"""LineScope.

Author: Mavs
Description: Run with linescope --memory -m examples.sample_package.

"""

from .helpers import calculate, prepare


def main(size: int = 20_000) -> None:
    """Run the package's order aggregation workload.

    Execute a nested cross-module call.

    Parameters
    ----------
    size : int, default=20_000
        Number of synthetic orders to prepare and aggregate.

    """
    orders = prepare(size)
    summaries = calculate(orders)
    for summary in summaries[:5]:
        print(summary)


if __name__ == "__main__":
    main()
