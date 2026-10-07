"""LineScope.

Author: Mavs
Description: Project discovery, filtering, and immutable source snapshots.

"""

from __future__ import annotations

from collections.abc import Iterable
import fnmatch
import linecache
import os
from pathlib import Path
import sys
import sysconfig
import tokenize

from linescope.enums import SourceKind
from linescope.model import SourceUnit


def discover_root(start: str | Path | None = None) -> Path:
    """Find the nearest project root above a file or directory.

    Parameters
    ----------
    start : str | [Path] | None, default=None
        Starting path. None uses the current working directory.

    Returns
    -------
    [Path]
        Nearest directory containing `pyproject.toml`, or the starting
        directory if no project configuration is present.

    """
    directory = Path(start or Path.cwd()).expanduser().resolve()

    if directory.is_file():
        directory = directory.parent

    for candidate in (directory, *directory.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate

    return directory


class SourceRegistry:
    """Identify project code and freeze its source on first observation.

    Parameters
    ----------
    root : str | [Path] | None, default=None
        Project root. None discovers the nearest `pyproject.toml`.

    include : Iterable[str], default=()
        Package names, paths, or glob patterns to include. An empty sequence
        includes Python files below the project root.

    exclude : Iterable[str], default=()
        Package names, paths, or glob patterns to exclude. Exclusions take
        precedence over inclusions.

    Attributes
    ----------
    root : [Path]
        Resolved project root used for file discovery and relative rules.

    include : tuple[str, ...]
        Package names, paths, or glob patterns selecting project source.

    exclude : tuple[str, ...]
        Exclusion rules applied before include rules.

    sources : dict[str, [SourceUnit]]
        Complete frozen source snapshots keyed by stable identifiers.

    _aliases : dict[str, str]
        Runtime notebook filenames mapped to stable snapshot identifiers.

    _accepted : dict[str, bool]
        Cached project ownership decisions for runtime filenames.

    _included_paths : tuple[[Path], ...]
        Candidate package and filesystem paths resolved without imports.

    _library_roots : tuple[[Path], ...]
        Standard-library and installed-package roots excluded from capture.

    _own_root : [Path]
        LineScope package directory excluded from project snapshots.

    See Also
    --------
    - linescope.source:discover_root
    - linescope.model:SourceUnit
    - linescope.source:SymbolIndex

    Examples
    --------
    ```pycon
    from linescope.source import SourceRegistry

    registry = SourceRegistry(include=["my_package"])
    registry.sources
    ```

    """

    def __init__(
        self,
        root: str | Path | None = None,
        include: Iterable[str] = (),
        exclude: Iterable[str] = (),
    ):
        """Initialize project discovery rules and frozen snapshot storage.

        Resolve inclusion candidates without importing project packages.

        Parameters
        ----------
        root : str | [Path] | None, default=None
            Project root used for source ownership or collector setup.

        include : Iterable[str], default=()
            Package, path, or glob rules selecting project source.

        exclude : Iterable[str], default=()
            Rules excluding source before inclusions are applied.

        """
        self.root = Path(root).expanduser().resolve() if root else discover_root()
        self.include = tuple(include)
        self.exclude = tuple(exclude)
        self.sources: dict[str, SourceUnit] = {}
        self._aliases: dict[str, str] = {}
        self._accepted: dict[str, bool] = {}
        self._included_paths = self._package_paths(self.include)
        self._library_roots = tuple(
            Path(path).resolve()
            for key, path in sysconfig.get_paths().items()
            if key in {"stdlib", "platstdlib", "purelib", "platlib"}
        )
        self._own_root = Path(__file__).resolve().parents[1]

    def _package_paths(self, rules: tuple[str, ...]) -> tuple[Path, ...]:
        """Resolve inclusion rules to candidate package and filesystem paths.

        Skip glob patterns and locate packages without executing their
        initializers.

        Parameters
        ----------
        rules : tuple[str, ...]
            Project source inclusion rules resolved without imports.

        Returns
        -------
        tuple[[Path], ...]
            Candidate filesystem locations for inclusion rules.

        """
        paths = []

        for rule in rules:
            if any(char in rule for char in "*?["):
                continue

            candidate = Path(rule).expanduser()

            if candidate.is_absolute():
                paths.append(candidate.resolve())
                continue

            for base in (self.root, self.root / "src"):
                paths.extend((base / rule, base / rule.replace(".", os.sep)))

            # Locate packages without importing them or executing __init__.py.
            package = rule.replace(".", os.sep)

            for entry in sys.path:
                base = Path(entry or Path.cwd()).resolve()

                paths.extend(
                    candidate.resolve()
                    for candidate in (base / package, base / f"{package}.py")
                    if candidate.exists()
                )

        return tuple(paths)

    def _matches(self, path: Path, rule: str) -> bool:
        """Match a file against a package name, path, or glob rule.

        Consider project-relative and normalized filesystem paths.

        Parameters
        ----------
        path : [Path]
            File, workspace, or import search path used by this operation.

        rule : str
            One package, path, or glob rule used for source selection.

        Returns
        -------
        bool
            Whether the path matches the supplied source selection rule.

        """
        normalized = rule.replace("\\", "/").rstrip("/")
        full = path.as_posix()

        try:
            relative = path.relative_to(self.root).as_posix()
        except ValueError:
            relative = full

        match_path = full if Path(rule).is_absolute() else relative

        if fnmatch.fnmatchcase(match_path, normalized):
            return True

        if not any(char in normalized for char in "*?["):
            rule_path = Path(rule).expanduser()
            candidate = rule_path if rule_path.is_absolute() else self.root / rule_path

            if path == candidate or path.is_relative_to(candidate):
                return True

            package_parts = normalized.replace(".", "/").split("/")
            parts = list(path.with_suffix("").parts)
            return any(
                parts[index : index + len(package_parts)] == package_parts
                for index in range(len(parts))
            )

        return False

    def accepts(self, filename: str | Path) -> bool:
        """Return whether a filename belongs in this profiling session.

        Parameters
        ----------
        filename : str | [Path]
            Runtime code filename or a registered notebook alias.

        Returns
        -------
        bool
            Whether source should be captured. Standard libraries, virtual
            environments, third-party packages, and LineScope are excluded.

        """
        name = str(filename)

        if name in self._aliases or name in self.sources:
            return True

        if name in self._accepted:
            return self._accepted[name]

        accepted = self._accepts_file(name)
        self._accepted[name] = accepted
        return accepted

    def _accepts_file(self, filename: str) -> bool:
        """Apply project inclusion and exclusion rules to a runtime filename.

        Hide LineScope, environments, standard libraries, and third-party
        internals.

        Parameters
        ----------
        filename : str
            Runtime filename being observed or resolved.

        Returns
        -------
        bool
            Whether the file belongs in captured project source.

        """
        if filename.startswith("<") or "://" in filename:
            return False

        try:
            path = Path(filename).expanduser().resolve()
        except (OSError, ValueError):
            return False

        if path.suffix.lower() not in {".py", ".pyw"}:
            return False

        blocked_parts = {"site-packages", "dist-packages", ".venv", "venv", ".tox", "__pycache__"}

        if any(part.lower() in blocked_parts for part in path.parts):
            return False

        if path.is_relative_to(self._own_root):
            return False

        if any(path.is_relative_to(library) for library in self._library_roots):
            return False

        if any(self._matches(path, rule) for rule in self.exclude):
            return False

        if self.include:
            return any(
                path == item or path.is_relative_to(item) for item in self._included_paths
            ) or any(self._matches(path, rule) for rule in self.include)

        return path.is_relative_to(self.root)

    def snapshot(self, filename: str | Path) -> SourceUnit | None:
        """Snapshot accepted source once, preserving the original text.

        Parameters
        ----------
        filename : str | [Path]
            File observed by a profiling event, or a notebook runtime alias.

        Returns
        -------
        [SourceUnit] | None
            Existing or newly captured source, or None for excluded or
            unavailable source. File encoding declarations are respected.

        """
        name = str(filename)

        if name in self._aliases:
            return self.sources[self._aliases[name]]

        if name in self.sources:
            return self.sources[name]

        if not self.accepts(name):
            return None

        path = Path(name).expanduser().resolve()
        source_id = path.as_posix()

        # Freeze the first observed contents. Reports must not change when
        # the user edits or removes the original file after collection.
        if source_id not in self.sources:
            try:
                with tokenize.open(path) as stream:
                    source = stream.read()
            except (OSError, UnicodeError, SyntaxError):
                return None

            self.sources[source_id] = SourceUnit(id=source_id, path=source_id, source=source)

        self._aliases[name] = source_id
        return self.sources[source_id]

    def register_notebook(
        self,
        source_id: str,
        source: str,
        path: str | None = None,
        filename: str | None = None,
    ) -> SourceUnit:
        """Register a notebook cell and its runtime filename.

        Parameters
        ----------
        source_id : str
            Stable identifier, usually `notebook://path#cell-N`.

        source : str
            Exact source captured when the cell runs.

        path : str | None, default=None
            Human-readable notebook path. None uses the identifier.

        filename : str | None, default=None
            Python code object's filename to map to this notebook cell.

        Returns
        -------
        [SourceUnit]
            Immutable first snapshot for this identifier. Reexecuted cells
            should use a new identifier if their source has changed.

        """
        unit = SourceUnit(
            id=source_id, path=path or source_id, source=source, kind=SourceKind.NOTEBOOK
        )
        return self.register(unit, filename)

    def register(self, unit: SourceUnit, filename: str | None = None) -> SourceUnit:
        """Register a captured source unit and an optional runtime alias.

        Parameters
        ----------
        unit : [SourceUnit]
            Source snapshot. An existing identifier retains its first source.

        filename : str | None, default=None
            Runtime code filename associated with the source unit.

        Returns
        -------
        [SourceUnit]
            Registered first snapshot.

        """
        if unit.id not in self.sources:
            self.sources[unit.id] = unit

        self._aliases[unit.id] = unit.id

        if filename:
            self._aliases[filename] = unit.id

        return self.sources[unit.id]

    def snapshot_cell(self, filename: str, path: str | None = None) -> SourceUnit | None:
        """Capture a cached IPython cell without interpreting its contents.

        Parameters
        ----------
        filename : str
            Runtime filename provided by IPython or Databricks.

        path : str | None, default=None
            Optional workspace notebook path.

        Returns
        -------
        [SourceUnit] | None
            Captured cell, or None when its source is not cached.

        """
        if filename in self._aliases:
            return self.sources[self._aliases[filename]]

        source = "".join(linecache.getlines(filename))

        if not source:
            return None

        source_id = f"notebook://{path or 'interactive'}#{filename.strip('<>')}"
        return self.register_notebook(source_id, source, path=path, filename=filename)
