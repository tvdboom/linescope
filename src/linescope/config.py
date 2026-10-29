"""LineScope.

Author: Mavs
Description: Resolve validated session options from project TOML and explicit
arguments.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from typing import Any

from linescope.enums import Backend, DisplayMode


def default_backend() -> Backend:
    """Select the portable default collector.

    Use Trace on every supported Python version.

    """
    return Backend.TRACE


def _validate_sample_rate(sample_rate: int | None):
    """Validate that the requested sampling rate is a positive integer.

    Reject boolean values and unsupported rates before collector startup.

    Parameters
    ----------
    sample_rate : int | None
        Requested sampling frequency in samples per second.

    """
    if sample_rate is not None:
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
            raise TypeError("sample_rate must be a positive integer or None")

        if sample_rate <= 0:
            raise ValueError("sample_rate must be a positive integer")


@dataclass(frozen=True)
class Config:
    """Configure a single profiling session.

    Parameters
    ----------
    backend : str | [Backend], default=default_backend()
        Built-in or registered collector name. Defaults to Trace.

    memory : bool, default=False
        Collect process RAM and retained Python allocations separately.

    gpu : bool, default=False
        Collect supported Scalene GPU utilization and device memory. Trace
        warns and continues Python profiling without GPU measurements.

    sample_rate : int | None, default=None
        Target samples per second for Scalene or Tachyon. None uses 1000.
        Trace ignores this setting; the achieved rate can vary.

    root : str | None, default=None
        Project directory; otherwise discover the nearest `pyproject.toml`.

    include : tuple[str, ...], default=()
        Package names, filesystem paths, or project-relative glob patterns.

    exclude : tuple[str, ...], default=()
        Package names, filesystem paths, or project-relative glob patterns.

    spark : bool, default=False
        Observe existing Spark actions. Requires PySpark; no actions are
        forced.

    notebooks : bool, default=True
        Capture executed notebook cells and their source links.

    child_notebooks : bool, default=True
        Profile Databricks child notebooks through temporary workspace copies.
        Requires SDK access and workspace permissions.

    display : str | [DisplayMode], default=[DisplayMode].END
        Full report at stop (`end`), cumulative report after each cell (`cell`),
        compact cell results (`cell-summary`), or no automatic display (`none`).

    output : str | None, default=None
        Explicit report destination. None opens a temporary HTML report.

    Attributes
    ----------
    backend : str | [Backend]
        Built-in or registered collector name. Defaults to Trace.

    memory : bool
        Collect process RAM and retained Python allocations separately.

    gpu : bool
        Collect supported Scalene GPU utilization and device memory. Trace
        warns and continues Python profiling without GPU measurements.

    root : str | None
        Project directory; otherwise discover the nearest `pyproject.toml`.

    include : tuple[str, ...]
        Package names, filesystem paths, or project-relative glob patterns.

    exclude : tuple[str, ...]
        Package names, filesystem paths, or project-relative glob patterns.

    spark : bool
        Observe existing Spark actions. Requires PySpark; no actions are
        forced.

    notebooks : bool
        Capture executed notebook cells and their source links.

    child_notebooks : bool
        Profile Databricks child notebooks through temporary workspace copies.
        Requires SDK access and workspace permissions.

    display : str | [DisplayMode]
        Full report at stop (`end`), cumulative report after each cell (`cell`),
        compact cell results (`cell-summary`), or no automatic display (`none`).

    output : str | None
        Explicit report destination. None opens a temporary HTML report.

    sample_rate : int | None
        Target samples per second for Scalene or Tachyon. None uses 1000.
        Trace ignores this setting; the achieved rate can vary.

    See Also
    --------
    - linescope:profile
    - linescope:Session

    Examples
    --------
    ```pycon
    >>> from linescope.config import Config

    >>> Config(backend="trace").memory
    False
    ```

    """

    backend: str | Backend = field(default_factory=default_backend)
    memory: bool = False
    gpu: bool = False
    root: str | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    spark: bool = False
    notebooks: bool = True
    child_notebooks: bool = True
    display: str | DisplayMode = DisplayMode.END
    output: str | None = None
    sample_rate: int | None = None

    def __post_init__(self):
        """Validate values and normalize built-in enum choices.

        Preserve registered custom backend names as strings.

        """
        _validate_sample_rate(self.sample_rate)

        if not isinstance(self.backend, str) or not self.backend:
            raise ValueError("backend must be a non-empty registered name")

        try:
            backend = Backend(self.backend)
        except ValueError:
            # Custom collectors have an open-ended registry of string names.
            pass
        else:
            object.__setattr__(self, "backend", backend)

        for name in ("memory", "gpu", "notebooks", "child_notebooks"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")

        try:
            object.__setattr__(self, "display", DisplayMode(self.display))
        except ValueError as error:
            raise ValueError("display must be 'end', 'cell', 'cell-summary', or 'none'") from error

        if not isinstance(self.spark, bool):
            raise ValueError("spark must be True or False")

        for name in ("include", "exclude"):
            value = getattr(self, name)

            if isinstance(value, str) or not isinstance(value, (list, tuple)):
                raise TypeError(f"{name} must be a sequence of strings")

            if any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"{name} entries must be non-empty strings")

            object.__setattr__(self, name, tuple(value))

        if self.root is not None:
            object.__setattr__(self, "root", str(Path(self.root).expanduser().resolve()))

        if self.output is not None and (not isinstance(self.output, str) or not self.output):
            raise ValueError("output must be a non-empty path")


def project_config(root: str | Path | None = None) -> dict[str, Any]:
    """Read the nearest project's `[tool.linescope]` configuration.

    Parameters
    ----------
    root : str | [Path] | None, default=None
        Starting directory for upward discovery.

    Returns
    -------
    dict[str, Any]
        Configuration keys; an absent project yields an empty mapping.

    """
    start = Path(root or Path.cwd()).expanduser().resolve()

    if start.is_file():
        start = start.parent

    for directory in (start, *start.parents):
        path = directory / "pyproject.toml"

        if path.is_file():
            with path.open("rb") as stream:
                values = dict(tomllib.load(stream).get("tool", {}).get("linescope", {}))

            if "root" in values:
                values["root"] = str((directory / values["root"]).resolve())
            else:
                values["root"] = str(directory)

            return values

    return {}


def resolve_config(**options: Any) -> Config:
    """Resolve the configuration for a new session.

    Explicit options override project settings and package defaults, in that
    order. Read project TOML for each session without retaining overrides.

    Parameters
    ----------
    **options : Any
        Fields accepted by [Config]. An explicit `root` selects the starting
        directory for project discovery; otherwise use the working directory.

    Returns
    -------
    [Config]
        Validated options owned by the new session.

    """
    values = project_config(options.get("root"))
    values.update(options)
    return Config(**values)
