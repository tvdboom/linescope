"""LineScope.

Author: Mavs
Description: Profile a custom package and follow calls between its modules.

"""

from pathlib import Path

from sample_package.__main__ import main as run_package

from linescope import profile


def main():
    """Profile the sample package and save its source report.

    Include the runner and package source while keeping unrelated examples
    outside the report. The package itself needs no profiling code.

    """
    with profile(
        memory=True,
        root=str(Path(__file__).parent),
        include=("package_example.py", "sample_package"),
        spark=False,
        display="none",
    ) as session:
        # Keep repeated scans practical with per-line RAM reads on older Python versions.
        run_package(size=400)

    report = session.save("package.html")
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
