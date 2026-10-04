"""LineScope.

Author: Mavs
Description: Define messages exchanged with the isolated Tachyon sampler.
Keep control commands and JSON message types consistent across processes.

"""

from enum import StrEnum


class SamplerMessage(StrEnum):
    """Identify sampler readiness, measurements, errors, and stop commands.

    String values form the parent and worker's process protocol.

    """

    READY = "ready"
    SAMPLE = "sample"
    ERROR = "error"
    STOP = "stop"
