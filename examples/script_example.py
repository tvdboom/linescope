"""LineScope.

Author: Mavs
Description: Compare two rolling-window implementations in a complete sensor
pipeline.

"""

from __future__ import annotations

from collections import defaultdict, deque
import csv
from dataclasses import dataclass
from io import StringIO
import math
from pathlib import Path
from random import Random
from statistics import fmean
from time import sleep

from linescope import profile


@dataclass(frozen=True)
class Reading:
    """Represent one generated weather observation in the profiling example.

    Attributes
    ----------
    station : str
        Weather station identifier associated with this reading.

    minute : int
        Minute index within the generated observation period.

    temperature : float
        Generated temperature used by the example aggregation.

    humidity : float
        Generated relative humidity used by the example aggregation.

    """

    station: str
    minute: int
    temperature: float
    humidity: float


def generate_csv(stations: int = 5, minutes: int = 360) -> str:
    """Compute generate csv.

    Generate reproducible sensor data with occasional outliers.

    """
    random = Random(42)
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["station", "minute", "temperature", "humidity"])
    for minute in range(minutes):
        for station in range(stations):
            temperature = 18 + station + 5 * math.sin(minute / 40) + random.gauss(0, 0.5)
            if minute % 97 == 0:
                temperature += 12
            humidity = 55 + 12 * math.cos(minute / 60) + random.gauss(0, 2)
            writer.writerow([f"station-{station}", minute, temperature, humidity])
    return buffer.getvalue()


def load_readings(source: str) -> list[Reading]:
    """Compute load readings.

    Simulate a short input wait, then parse and validate CSV records.

    """
    sleep(0.03)
    readings = []
    for record in csv.DictReader(StringIO(source)):
        temperature, humidity = float(record["temperature"]), float(record["humidity"])
        if math.isfinite(temperature) and 0 <= humidity <= 100:
            readings.append(
                Reading(record["station"], int(record["minute"]), temperature, humidity)
            )
    return readings


def group_readings(readings: list[Reading]) -> dict[str, list[Reading]]:
    """Compute group readings.

    Group records by station and order them by timestamp.

    """
    grouped = defaultdict(list)
    for reading in readings:
        grouped[reading.station].append(reading)
    return {
        station: sorted(values, key=lambda item: item.minute)
        for station, values in grouped.items()
    }


def rolling_slow(values: list[float], window: int = 24) -> list[float]:
    """Compute rolling slow.

    Recalculate each rolling average using a fresh slice and Python loop.

    """
    averages = []
    for index in range(len(values)):
        segment = values[max(0, index - window + 1) : index + 1]
        total = 0.0
        for value in segment:
            total += value
        averages.append(total / len(segment))
    return averages


def rolling_fast(values: list[float], window: int = 24) -> list[float]:
    """Compute rolling fast.

    Maintain the same rolling average with a bounded queue and running sum.

    """
    recent = deque()
    total = 0.0
    averages = []
    for value in values:
        recent.append(value)
        total += value
        if len(recent) > window:
            total -= recent.popleft()
        averages.append(total / len(recent))
    return averages


def analyze(grouped: dict[str, list[Reading]]) -> list[dict[str, float | str | int]]:
    """Compute analyze.

    Compare results and identify readings far above the rolling baseline.

    """
    summaries = []
    for station, readings in grouped.items():
        temperatures = [reading.temperature for reading in readings]
        slow, fast = rolling_slow(temperatures), rolling_fast(temperatures)
        assert all(math.isclose(left, right) for left, right in zip(slow, fast, strict=True))
        anomalies = sum(
            value - baseline > 8 for value, baseline in zip(temperatures, fast, strict=True)
        )
        summaries.append(
            {
                "station": station,
                "readings": len(readings),
                "mean": fmean(temperatures),
                "anomalies": anomalies,
            }
        )
    return summaries


def main():
    """Run main.

    Run the pipeline and open one HTML report in a new browser tab.

    """
    with profile(memory=True, root=str(Path(__file__).parent), spark=False):
        # Cover full rolling windows and repeated outliers with per-line RAM reads.
        source = generate_csv(stations=4, minutes=120)
        readings = load_readings(source)
        analyze(group_readings(readings))


if __name__ == "__main__":
    main()
