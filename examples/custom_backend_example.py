"""LineScope.

Author: Mavs
Description: Register a custom backend using the Scalene sampling collector.

"""

from collections import Counter
import hashlib
from pathlib import Path
import re

from linescope import profile, register_backend
from linescope.backends.scalene import ScaleneBackend


class ProjectScalene(ScaleneBackend):
    """Collect project samples.

    Collect Scalene measurements under a project-specific backend name.

    Attributes
    ----------
    name : str
        Collector registry name used by this example or test subclass.

    """

    name = "project-scalene"


def main() -> None:
    """Run main.

    Register the collector and save a report.

    """
    register_backend("project-scalene", ProjectScalene)

    with profile(
        backend="project-scalene", memory=True, display="none", root=str(Path(__file__).parent)
    ) as session:
        # Keep a visible allocation workload practical for per-line RAM collection.
        documents = [
            f"Document {number}: Python profiling makes repeated work visible. "
            "Compare collection backends, inspect source, and improve the expensive lines."
            for number in range(1_000)
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
