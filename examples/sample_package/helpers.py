"""LineScope.

Author: Mavs
Description: Helpers with source definitions in a second module.

"""

from collections import defaultdict
from dataclasses import dataclass
from random import Random
from time import sleep


@dataclass(frozen=True)
class Order:
    """Represent a synthetic order.

    Keep records immutable while comparing aggregation strategies.

    """

    region: str
    product: str
    quantity: int
    price_cents: int


def prepare(size: int) -> list[Order]:
    """Compute prepare.

    Generate deterministic input after a small I/O-like wait.

    """
    sleep(0.01)
    random = Random(42)
    regions = ("north", "south", "east", "west")
    products = {"book": 1400, "pen": 250, "desk": 18000, "chair": 9500}
    return [
        Order(
            random.choice(regions),
            product := random.choice(tuple(products)),
            random.randint(1, 5),
            products[product],
        )
        for _ in range(size)
    ]


def calculate(values: list[Order]) -> list[dict[str, str | int]]:
    """Compute calculate.

    Aggregate revenue and units, then compare with repeated filtering.

    """
    revenue: dict[tuple[str, str], int] = defaultdict(int)
    units: dict[tuple[str, str], int] = defaultdict(int)
    for order in values:
        key = (order.region, order.product)
        revenue[key] += order.quantity * order.price_cents
        units[key] += order.quantity

    # Compare an intentionally expensive repeated scan with the single pass.
    verified = {
        key: sum(
            order.quantity * order.price_cents
            for order in values
            if (order.region, order.product) == key
        )
        for key in revenue
    }
    assert dict(revenue) == verified
    return [
        {"region": region, "product": product, "revenue_cents": total, "units": units[key]}
        for key, total in sorted(revenue.items(), key=lambda item: item[1], reverse=True)
        for region, product in [key]
    ]
