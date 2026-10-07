"""LineScope.

Author: Mavs
Description: Locate sustained RAM growth and a temporary processing spike.

"""

from pathlib import Path
from time import sleep

from linescope import profile


def process_data(data: bytearray) -> int:
    """Process input with a temporary buffer large enough to expose a spike.

    Parameters
    ----------
    data : bytearray
        Retained input buffer.

    Returns
    -------
    int
        Combined input and temporary buffer size, in bytes.

    """
    temporary = bytearray(48 * 1024**2)
    sleep(0.03)
    return len(data) + len(temporary)


def main():
    """Profile RAM growth and save a source-linked memory timeline.

    Open the Memory view to inspect the temporary spike and repeated growth.
    Allocators may keep released pages for reuse after the buffers are freed.

    """
    with profile(
        memory=True,
        root=str(Path(__file__).parent),
        include=("memory_example.py",),
        spark=False,
        notebooks=False,
        display="none",
    ) as session:
        data = bytearray(16 * 1024**2)
        process_data(data)
        batches = []
        for _ in range(4):
            # Keep each growth step on a visible source line in the report.
            batches.append(bytearray(4 * 1024**2))  # noqa: PERF401
        del batches
        del data

    report = session.save("memory.html")
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
