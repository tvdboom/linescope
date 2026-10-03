"""LineScope.

Author: Mavs
Description: Validated configuration with project, global, and explicit precedence.

"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class Config:
    """Configure a single profiling session.

    Parameters
    ----------
    backend : str, default="scalene"
        Registered measurement engine. Use `trace` for portable instrumentation.

    memory : bool, default=False
        Collect Python driver memory if the backend supports it.

    root : str | None, default=None
        Project directory; otherwise discover the nearest `pyproject.toml`.

    include, exclude : tuple[str, ...], default=()
        Package names, filesystem paths, or project-relative glob patterns.

    spark : bool | str, default="auto"
        Observe driver actions in an already loaded Spark environment.

    notebooks : bool, default=True
        Snapshot notebook cells and instrument available notebook integrations.

    display : str, default="end"
        Display once at completion, after each cell, or never (`none`).

    output : str, default="linescope.html"
        Report path for automatic display outside notebooks.

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

    backend: str = "scalene"
    memory: bool = False
    root: str | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    spark: bool | Literal["auto"] = "auto"
    notebooks: bool = True
    display: Literal["end", "cell", "none"] = "end"
    output: str = "linescope.html"

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str) or not self.backend:
            raise ValueError("backend must be a non-empty registered name")
        for name in ("memory", "notebooks"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.display not in ("end", "cell", "none"):
            raise ValueError("display must be 'end', 'cell', or 'none'")
        if self.spark != "auto" and not isinstance(self.spark, bool):
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
        if not isinstance(self.output, str) or not self.output:
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
    dict
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
    """Resolve explicit options over configured globals, project settings, and defaults."""
    values = project_config(options.get("root", _overrides.get("root")))
    values.update(_overrides)
    values.update(options)
    return Config(**values)


def configure(**options: Any) -> Config:
    """Set validated defaults for subsequent sessions without changing active ones.

    Parameters
    ----------
    **options
        Fields accepted by [Config]. Explicit session arguments take precedence.

    Returns
    -------
    Config
        Effective configuration after applying the requested defaults.

    Examples
    --------
    ```pycon
    >>> from linescope import configure
    >>> configure(backend="trace", display="none").backend
    'trace'
    ```
    """
    effective = resolve_config(**options)
    normalized = asdict(effective)
    _overrides.update({name: normalized[name] for name in options})
    return effective
