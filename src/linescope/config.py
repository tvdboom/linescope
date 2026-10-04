"""LineScope.

Author: Mavs
Description: Validated configuration with project, global, and explicit
precedence.

"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import sys
import tomllib
from typing import Any

from linescope.enums import Backend, DisplayMode, SparkMode


def default_backend() -> Backend:
    """Select the runtime's supported default collector.

    Use Trace on Python 3.15 and Scalene on earlier supported runtimes.

    """
    return Backend.TRACE if sys.version_info >= (3, 15) else Backend.SCALENE


@dataclass(frozen=True)
class Config:
    """Configure a single profiling session.

    Parameters
    ----------
    backend : [Backend] | str, default=default_backend()
        Built-in backend member, its string value, or a registered engine
        name. Default to Scalene on Python 3.11-3.14 and Trace on Python
        3.15. Select `tachyon` for Python 3.15 sampling.

    memory : bool, default=False
        Collect Python driver memory if the backend supports it.

    gpu : bool, default=False
        Collect supported Scalene GPU utilization and device memory.

    root : str | None, default=None
        Project directory; otherwise discover the nearest `pyproject.toml`.

    include : tuple[str, ...], default=()
        Package names, filesystem paths, or project-relative glob patterns.

    exclude : tuple[str, ...], default=()
        Package names, filesystem paths, or project-relative glob patterns.

    spark : bool | [SparkMode] | str, default=[SparkMode].AUTO
        Observe driver actions in an already loaded Spark environment.

    notebooks : bool, default=True
        Snapshot notebook cells and instrument available notebook
        integrations.

    display : [DisplayMode] | str, default=[DisplayMode].END
        Display once at completion, after each cell, or never (`none`).

    inline : bool, default=False
        Display inside a notebook cell instead of opening a new browser tab.

    output : str | None, default=None
        Explicit report destination. None opens a temporary HTML report.

    See Also
    --------
    - linescope:configure
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

    backend: Backend | str = field(default_factory=default_backend)
    memory: bool = False
    gpu: bool = False
    root: str | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    spark: bool | SparkMode | str = SparkMode.AUTO
    notebooks: bool = True
    display: DisplayMode | str = DisplayMode.END
    inline: bool = False
    output: str | None = None

    def __post_init__(self) -> None:
        """Validate values and normalize built-in enum choices.

        Preserve registered custom backend names as strings.

        """
        if not isinstance(self.backend, str) or not self.backend:
            raise ValueError("backend must be a non-empty registered name")

        try:
            backend = Backend(self.backend)
        except ValueError:
            # Custom collectors have an open-ended registry of string names.
            pass
        else:
            object.__setattr__(self, "backend", backend)

        for name in ("memory", "gpu", "notebooks", "inline"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")

        try:
            object.__setattr__(self, "display", DisplayMode(self.display))
        except ValueError as error:
            raise ValueError("display must be 'end', 'cell', or 'none'") from error

        if isinstance(self.spark, str):
            try:
                object.__setattr__(self, "spark", SparkMode(self.spark))
            except ValueError as error:
                raise ValueError("spark must be True, False, or 'auto'") from error
        elif not isinstance(self.spark, bool):
            raise ValueError("spark must be True, False, or 'auto'")

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


_overrides: dict[str, Any] = {}


def project_config(root: str | Path | None = None) -> dict[str, Any]:
    """Read the nearest project's `[tool.linescope]` configuration.

    Parameters
    ----------
    root : str | Path | None, default=None
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

    Explicit options override process defaults, project settings, and package
    defaults, in that order.

    """
    values = project_config(options.get("root", _overrides.get("root")))
    values.update(_overrides)
    values.update(options)
    return Config(**values)


def configure(**options: Any) -> Config:
    """Set validated defaults for subsequent sessions.

    Active sessions keep their existing configuration.

    Parameters
    ----------
    **options
        Fields accepted by [Config]. Explicit session arguments take
        precedence.

    Returns
    -------
    [Config]
        Effective configuration after applying the requested defaults.

    Examples
    --------
    ```pycon
    >>> from linescope import configure
    >>> str(configure(backend="trace", display="none").backend)
    'trace'
    ```

    """
    effective = resolve_config(**options)
    normalized = asdict(effective)
    _overrides.update({name: normalized[name] for name in options})
    return effective
