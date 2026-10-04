"""LineScope.

Author: Mavs
Description: Run with linescope --backend trace -m examples.sample_package.

"""

from .helpers import calculate, prepare


def main():
    """Run main.

    Execute a nested cross-module call.

    """
    orders = prepare(20_000)
    summaries = calculate(orders)
    for summary in summaries[:5]:
        print(summary)


if __name__ == "__main__":
    main()
