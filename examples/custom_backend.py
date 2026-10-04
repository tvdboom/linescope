"""LineScope.

Author: Mavs
Description: Register a custom backend using the portable trace collector.

"""

from collections import Counter
import hashlib
from pathlib import Path
import re

from linescope import profile, register_backend
from linescope.backends.trace import TraceBackend


class ProjectTrace(TraceBackend):
    """Compute project trace.

    Collect trace measurements under a project-specific backend name.

    """

    name = "project-trace"


def main() -> None:
    """Run main.

    Register the collector and save a report.

    """
    register_backend("project-trace", ProjectTrace)

    with profile(
        backend="project-trace", display="none", root=str(Path(__file__).parent)
    ) as session:
        documents = [
            f"Document {number}: Python profiling makes repeated work visible. "
            "Compare collection backends, inspect source, and improve the expensive lines."
            for number in range(4000)
        ]
        tokenized = [re.findall(r"[a-z]+", document.lower()) for document in documents]
        frequencies = Counter(word for words in tokenized for word in words)
        common = frequencies.most_common(8)
        signatures = [hashlib.sha256(document.encode()).hexdigest() for document in documents]
        total = sum(frequencies.values())

    report = session.save("custom-backend.html")
    print(f"Total: {total}; report: {report}")
    print(f"Common words: {common}; unique documents: {len(set(signatures))}")


if __name__ == "__main__":
    main()
