"""LineScope.

Author: Mavs
Description: Conservative AST-based links between snapshotted project symbols.

"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
import posixpath
import re

from linescope.enums import SourceKind, SymbolKind
from linescope.model import SourceLocation, SourceUnit, SymbolDefinition, SymbolRef


class _BindingKind(StrEnum):
    """Classify lexical scopes, bindings, and navigation targets.

    Keep unresolved bindings distinct from known definitions and imports.

    """

    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    MODULE = "module"
    UNKNOWN = "unknown"
    INSTANCE = "instance"
    IMPORT = "import"
    ALIAS = "alias"
    NAMESPACE = "namespace"


class _RedirectKind(StrEnum):
    """Identify declarations that redirect assignments to an outer scope.

    Global declarations target the module; nonlocal declarations target a
    containing function.

    """

    GLOBAL = "global"
    NONLOCAL = "nonlocal"


@dataclass(eq=False)
class _Scope:
    unit: SourceUnit
    kind: _BindingKind
    parent: _Scope | None = None
    qualified_name: str = ""
    bindings: dict[str, list[_Binding]] = field(default_factory=lambda: defaultdict(list))
    redirects: set[str] = field(default_factory=set)
    redirect_kinds: dict[str, _RedirectKind] = field(default_factory=dict)
    definition: SymbolDefinition | None = None
    dynamic_class: bool = False


@dataclass
class _Binding:
    kind: _BindingKind
    scope: _Scope
    definition: SymbolDefinition | None = None
    expression: ast.expr | None = None
    module: str = ""
    member: str | None = None
    level: int = 0


@dataclass
class _Target:
    kind: _BindingKind
    scope: _Scope
    definition: SymbolDefinition | None = None
    module_name: str = ""


def _character_column(source: str, line: int, byte_column: int) -> int:
    """Translate Python AST UTF-8 byte columns into source character offsets.

    Match browser offsets even when a source line contains non-ASCII text.

    """
    lines = source.splitlines()

    if not 0 < line <= len(lines):
        return 0

    return len(lines[line - 1].encode("utf-8")[:byte_column].decode("utf-8"))


def _notebook_path(unit: SourceUnit) -> str:
    path = unit.id if unit.id.startswith("notebook://") else unit.path.split(" · cell ", 1)[0]
    return path.split("#", 1)[0].removeprefix("notebook://").rstrip("/")


class _Collector(ast.NodeVisitor):
    """Collect lexical bindings without running imports or user code.

    Record lexical scope and binding changes for conservative navigation.

    """

    def __init__(self, unit: SourceUnit, definitions: list[SymbolDefinition]) -> None:
        self.scope = _Scope(unit, _BindingKind.MODULE)
        self.root = self.scope
        self.scopes: dict[ast.AST, _Scope] = {}
        self.definitions = definitions
        self.assigned_attributes: set[str] = set()

    def visit(self, node: ast.AST) -> None:
        self.scopes[node] = self.scope
        super().visit(node)

    def _bind(self, name: str, binding: _Binding | None = None) -> None:
        self.scope.bindings[name].append(binding or _Binding(_BindingKind.UNKNOWN, self.scope))

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        outer = self.scope
        kind = _BindingKind.CLASS if isinstance(node, ast.ClassDef) else _BindingKind.FUNCTION

        if kind == _BindingKind.FUNCTION and outer.kind == _BindingKind.CLASS:
            kind = _BindingKind.METHOD

        qualified_name = ".".join(filter(None, (outer.qualified_name, node.name)))
        definition = SymbolDefinition(
            kind=SymbolKind(kind.value),
            qualified_name=qualified_name,
            source_id=outer.unit.id,
            line=node.lineno,
            column=_character_column(outer.unit.source, node.lineno, node.col_offset),
        )
        self.definitions.append(definition)
        inner = _Scope(outer.unit, kind, outer, qualified_name, definition=definition)

        if isinstance(node, ast.ClassDef):
            inner.dynamic_class = bool(node.decorator_list) or any(
                item.arg == "metaclass" for item in node.keywords
            )

        self._bind(node.name, _Binding(kind, inner, definition=definition))

        for decorator in node.decorator_list:
            self.visit(decorator)

        if isinstance(node, ast.ClassDef):
            for item in [*node.bases, *node.keywords]:
                self.visit(item)
        else:
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if default:
                    self.visit(default)

            if node.returns:
                self.visit(node.returns)

        self.scope = inner

        if not isinstance(node, ast.ClassDef):
            self._parameters(node.args)
            positional = [*node.args.posonlyargs, *node.args.args]
            decorators = [item.id for item in node.decorator_list if isinstance(item, ast.Name)]

            if (
                outer.kind == _BindingKind.CLASS
                and positional
                and "staticmethod" not in decorators
            ):
                # The first argument of an ordinary method is the instance;
                # classmethods bind the class, and both permit method lookup.
                first = positional[0].arg
                inner.bindings[first] = [
                    _Binding(_BindingKind.INSTANCE, outer, definition=outer.definition)
                ]

        for item in node.body:
            self.visit(item)

        self.scope = outer

    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition
    visit_ClassDef = _definition

    def _parameters(self, args: ast.arguments) -> None:
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            self._bind(arg.arg)

        for arg in (args.vararg, args.kwarg):
            if arg:
                self._bind(arg.arg)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default:
                self.visit(default)

        outer = self.scope
        self.scope = _Scope(outer.unit, _BindingKind.FUNCTION, outer)
        self._parameters(node.args)
        self.visit(node.body)
        self.scope = outer

    def _comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ) -> None:
        outer = self.scope
        # The first iterable is evaluated in the enclosing lexical scope.
        self.visit(node.generators[0].iter)
        self.scope = _Scope(outer.unit, _BindingKind.FUNCTION, outer)

        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)

            self.visit(generator.target)

            for condition in generator.ifs:
                self.visit(condition)

        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)

        self.scope = outer

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_DictComp = _comprehension
    visit_GeneratorExp = _comprehension

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module = alias.name if alias.asname else alias.name.split(".")[0]
            self._bind(
                alias.asname or module, _Binding(_BindingKind.IMPORT, self.scope, module=module)
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self._bind(
                    alias.asname or alias.name,
                    _Binding(
                        _BindingKind.IMPORT,
                        self.scope,
                        module=node.module or "",
                        member=alias.name,
                        level=node.level,
                    ),
                )

    def _assignment(self, target: ast.expr, value: ast.expr | None) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id, _Binding(_BindingKind.ALIAS, self.scope, expression=value))
        else:
            self.visit(target)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._assignment(target, node.value)

        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assignment(node.target, node.value)

        if node.value:
            self.visit(node.value)

        self.visit(node.annotation)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._assignment(node.target, node.value)
        self.visit(node.value)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.assigned_attributes.add(node.attr)

        self.visit(node.value)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name)

        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        # Assignment through global/nonlocal is dynamic across calls. We do
        # not infer links through these declarations.
        self.scope.redirects.update(node.names)
        self.scope.redirect_kinds.update(dict.fromkeys(node.names, _RedirectKind.GLOBAL))

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.scope.redirects.update(node.names)
        self.scope.redirect_kinds.update(dict.fromkeys(node.names, _RedirectKind.NONLOCAL))

    def invalidate_mutations(self) -> None:
        """Invalidate outer bindings that nested functions may replace.

        Treat nonlocal and global assignments as potential rebinding across
        calls.

        """
        for scope in set(self.scopes.values()):
            for name, kind in scope.redirect_kinds.items():
                if name not in scope.bindings:
                    continue

                target = scope.parent

                if kind == _RedirectKind.GLOBAL:
                    target = self.root
                else:
                    while target and (
                        target.kind == _BindingKind.CLASS or name not in target.bindings
                    ):
                        target = target.parent

                if target:
                    target.bindings[name].append(_Binding(_BindingKind.UNKNOWN, target))

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._bind(node.name)

        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._bind(node.rest)

        self.generic_visit(node)


class SymbolIndex:
    """Index functions, classes, methods, and resolvable calls in snapshots.

    Resolution deliberately omits ambiguous imports, rebound names, dynamic
    dispatch, and third-party targets. No user source or imports are
    executed. Columns are Unicode character offsets, matching the embedded
    report text.

    Parameters
    ----------
    sources : dict[str, [SourceUnit]]
        Project files and notebook cells captured during a profile.

    Attributes
    ----------
    definitions : list[[SymbolDefinition]]
        Lexical project definitions, including nested functions and methods.

    references : dict[tuple[str, int], list[[SymbolRef]]]
        Individually clickable call tokens grouped by source and line.

    See Also
    --------
    - linescope.source:SourceRegistry
    - linescope.model:SymbolDefinition
    - linescope.source:build_navigation

    Examples
    --------
    ```pycon
    from linescope.model import SourceUnit
    from linescope.source import SymbolIndex
    code = "def double(value): return value * 2"
    source = SourceUnit("demo", "example.py", code)
    index = SymbolIndex({source.id: source})
    [definition.qualified_name for definition in index.definitions]
    ```

    """

    def __init__(self, sources: dict[str, SourceUnit]) -> None:
        self.sources = dict(sources)
        self.definitions: list[SymbolDefinition] = []
        self.references: dict[tuple[str, int], list[SymbolRef]] = defaultdict(list)
        self._collectors: dict[str, _Collector] = {}
        self._trees: dict[str, ast.Module] = {}
        self._modules: dict[str, list[_Scope]] = defaultdict(list)
        self._paths: dict[str, _Scope] = {}
        self._assigned_attributes: set[str] = set()

        for unit in sources.values():
            self._collect(unit)

        for source_id, tree in self._trees.items():
            self._link(source_id, tree)

    def _collect(self, unit: SourceUnit) -> None:
        source = unit.source

        if unit.kind == SourceKind.NOTEBOOK:
            # IPython line magics are not Python syntax; retain their lines so
            # every remaining AST position still refers to the snapshot.
            source = "\n".join(re.sub(r"^(\s*)[!%]", r"\1#", line) for line in source.split("\n"))

        try:
            tree = ast.parse(source, filename=unit.path)
        except (SyntaxError, ValueError):
            return

        collector = _Collector(unit, self.definitions)
        collector.visit(tree)
        collector.invalidate_mutations()
        self._collectors[unit.id] = collector
        self._trees[unit.id] = tree
        self._assigned_attributes.update(collector.assigned_attributes)

        if unit.kind == SourceKind.PYTHON:
            path = PurePosixPath(unit.path.replace("\\", "/"))
            self._paths[str(path)] = collector.root
            parts = list(path.with_suffix("").parts)

            if parts[-1] == "__init__":
                parts.pop()

            # Imported module names are suffixes of the filesystem path. Only
            # unique candidates will resolve; duplicate package roots do not.
            for index in range(len(parts)):
                candidate = parts[index:]

                if all(part.isidentifier() for part in candidate):
                    self._modules[".".join(candidate)].append(collector.root)

    def _module(self, name: str, scope: _Scope, level: int = 0) -> _Scope | None:
        if level:
            parent = PurePosixPath(scope.unit.path.replace("\\", "/")).parent

            for _ in range(level - 1):
                parent = parent.parent

            target = parent.joinpath(*name.split(".")) if name else parent
            candidates = (str(target.with_suffix(".py")), str(target / "__init__.py"))
            matches = [self._paths[item] for item in candidates if item in self._paths]
        else:
            matches = self._modules.get(name, [])

        return matches[0] if len(matches) == 1 else None

    def _peers(self, scope: _Scope) -> list[_Scope]:
        if scope.unit.kind != SourceKind.NOTEBOOK:
            return []

        path = _notebook_path(scope.unit)
        imports = []

        for unit in self.sources.values():
            if unit.kind == SourceKind.NOTEBOOK and _notebook_path(unit) == path:
                for match in re.finditer(
                    r"^\s*%run\s+[\"']?([^\s\"']+)", unit.source, re.MULTILINE
                ):
                    target = match.group(1)

                    if not target.startswith("/"):
                        target = posixpath.normpath(
                            posixpath.join(posixpath.dirname(path), target)
                        )

                    imports.append(target)

        return [
            collector.root
            for collector in self._collectors.values()
            if collector.root is not scope
            and collector.root.unit.kind == SourceKind.NOTEBOOK
            and _notebook_path(collector.root.unit) in [path, *imports]
        ]

    def _name(self, name: str, scope: _Scope, seen: set[tuple[int, str]]) -> _Target | None:
        key = (id(scope), name)

        if key in seen or name in scope.redirects:
            return None

        seen = {*seen, key}
        bindings = scope.bindings.get(name, [])

        if scope.kind == _BindingKind.MODULE:
            bindings = [
                *bindings,
                *(
                    binding
                    for peer in self._peers(scope)
                    for binding in peer.bindings.get(name, [])
                ),
            ]

        if len(bindings) > 1:
            return None

        if bindings:
            return self._binding(bindings[0], seen)

        parent = scope.parent

        # A function's unqualified identifiers never capture class locals.
        if scope.kind in {_BindingKind.FUNCTION, _BindingKind.METHOD}:
            while parent and parent.kind == _BindingKind.CLASS:
                parent = parent.parent

        return self._name(name, parent, seen) if parent else None

    def _binding(self, binding: _Binding, seen: set[tuple[int, str]]) -> _Target | None:
        if binding.kind in {
            _BindingKind.FUNCTION,
            _BindingKind.CLASS,
            _BindingKind.METHOD,
            _BindingKind.INSTANCE,
        }:
            return _Target(binding.kind, binding.scope, binding.definition)

        if binding.kind == _BindingKind.ALIAS and binding.expression:
            return self._expression(binding.expression, binding.scope, seen)

        if binding.kind == _BindingKind.IMPORT:
            module = self._module(binding.module, binding.scope, binding.level)

            if binding.member:
                if module:
                    target = self._name(binding.member, module, seen)

                    if target:
                        return target

                submodule = ".".join(filter(None, (binding.module, binding.member)))
                module = self._module(submodule, binding.scope, binding.level)

            if module:
                return _Target(_BindingKind.MODULE, module)

            if not binding.member and not binding.level and self._has_namespace(binding.module):
                return _Target(_BindingKind.NAMESPACE, binding.scope, module_name=binding.module)

        return None

    def _has_namespace(self, name: str) -> bool:
        return any(candidate.startswith(f"{name}.") for candidate in self._modules)

    def _attribute(self, owner: _Target, attr: str, seen: set[tuple[int, str]]) -> _Target | None:
        if attr in self._assigned_attributes:
            return None

        if owner.kind == _BindingKind.NAMESPACE:
            name = f"{owner.module_name}.{attr}"
            module = self._module(name, owner.scope)

            if module:
                return _Target(_BindingKind.MODULE, module)

            if self._has_namespace(name):
                return _Target(_BindingKind.NAMESPACE, owner.scope, module_name=name)

            return None

        if owner.kind == _BindingKind.MODULE:
            target = self._name(attr, owner.scope, seen)

            if target:
                return target

            path = PurePosixPath(owner.scope.unit.path.replace("\\", "/"))

            if path.name == "__init__.py":
                for candidate in (path.parent / f"{attr}.py", path.parent / attr / "__init__.py"):
                    if str(candidate) in self._paths:
                        return _Target(_BindingKind.MODULE, self._paths[str(candidate)])

        if owner.kind in {_BindingKind.CLASS, _BindingKind.INSTANCE}:
            if owner.scope.dynamic_class or any(
                name in owner.scope.bindings
                for name in ("__getattr__", "__getattribute__", "__new__")
            ):
                return None

            bindings = owner.scope.bindings.get(attr, [])

            if len(bindings) == 1:
                return self._binding(bindings[0], seen)

        return None

    def _expression(
        self,
        node: ast.expr,
        scope: _Scope,
        seen: set[tuple[int, str]],
    ) -> _Target | None:
        if isinstance(node, ast.Name):
            return self._name(node.id, scope, seen)

        if isinstance(node, ast.Attribute):
            owner = self._expression(node.value, scope, seen)
            return self._attribute(owner, node.attr, seen) if owner else None

        if isinstance(node, ast.Call):
            target = self._expression(node.func, scope, seen)

            if target and target.kind == _BindingKind.CLASS:
                return _Target(_BindingKind.INSTANCE, target.scope, target.definition)

        return None

    def _link(self, source_id: str, tree: ast.Module) -> None:
        collector = self._collectors[source_id]
        unit = collector.root.unit

        if unit.kind == SourceKind.NOTEBOOK:
            for line_number, text in enumerate(unit.source.splitlines(), 1):
                match = re.match(r"""\s*%run\s+(?:(["'])(.*?)\1|([^\s]+))""", text)

                if match:
                    group = 2 if match.group(1) else 3
                    path = match.group(group)

                    if not path.startswith("-"):
                        self._link_notebook(
                            unit, path, line_number, match.start(group), match.end(group)
                        )

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "run"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "notebook"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "dbutils"
            ):
                argument = (
                    node.args[0]
                    if node.args
                    else next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "path"), None
                    )
                )

                if (
                    isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                    and argument.lineno == argument.end_lineno
                ):
                    self._link_notebook(
                        unit,
                        argument.value,
                        argument.lineno,
                        _character_column(unit.source, argument.lineno, argument.col_offset),
                        _character_column(
                            unit.source, argument.lineno, argument.end_col_offset or 0
                        ),
                    )

            scope = collector.scopes[node]
            target = self._expression(node.func, scope, set())

            if not target or not target.definition or target.kind == _BindingKind.INSTANCE:
                continue

            if isinstance(node.func, ast.Name):
                name = node.func.id
                line = node.func.lineno
                start = _character_column(unit.source, line, node.func.col_offset)
                end = _character_column(
                    unit.source, line, node.func.end_col_offset or node.func.col_offset
                )
            elif isinstance(node.func, ast.Attribute):
                line = node.func.end_lineno or node.func.lineno
                end = _character_column(unit.source, line, node.func.end_col_offset or 0)
                text = unit.source.splitlines()[line - 1]
                # Python normalizes identifiers to NFKC in the AST. Derive
                # the actual token from the original spelling, which may
                # have a different length (for example the ligature ﬃ).
                start = next(index for index in range(end) if text[index:end].isidentifier())
                name = text[start:end]
            else:
                continue

            definition = target.definition
            self.references[(source_id, line)].append(
                SymbolRef(
                    name=name,
                    line=line,
                    column=start,
                    end_column=end,
                    target=SourceLocation(
                        definition.source_id,
                        definition.line,
                        definition.column,
                        definition.qualified_name,
                    ),
                )
            )

        for references in self.references.values():
            references.sort(key=lambda reference: reference.column)

    def _link_notebook(self, unit: SourceUnit, path: str, line: int, start: int, end: int) -> None:
        resolved = path

        if not path.startswith("/") and unit.kind == SourceKind.NOTEBOOK:
            resolved = posixpath.join(posixpath.dirname(_notebook_path(unit)), path)

        resolved = posixpath.normpath(resolved)
        candidates = [
            item
            for item in self.sources.values()
            if item.kind == SourceKind.NOTEBOOK and _notebook_path(item) == resolved
        ]

        if candidates:
            # Prefer the first captured cell over an unavailable-source placeholder.
            target = next((item for item in candidates if "#cell-" in item.id), candidates[0])
            self.references[(unit.id, line)].append(
                SymbolRef(path, line, start, end, SourceLocation(target.id, 1, symbol=resolved))
            )


def build_navigation(
    sources: dict[str, SourceUnit],
) -> tuple[list[SymbolDefinition], dict[tuple[str, int], list[SymbolRef]]]:
    r"""Build per-token navigation from captured project and notebook source.

    Parameters
    ----------
    sources : dict[str, [SourceUnit]]
        Immutable source snapshots keyed by source identifier.

    Returns
    -------
    list[[SymbolDefinition]]
        Definitions suitable for the report's function and class index.

    dict[tuple[str, int], list[[SymbolRef]]]
        Call references grouped by source identifier and one-based line.

    Examples
    --------
    ```pycon
    >>> from linescope.model import SourceUnit
    >>> from linescope.source import build_navigation
    >>> source = SourceUnit("demo.py", "demo.py", "def work(): pass\nwork()\n")
    >>> definitions, references = build_navigation({source.id: source})
    >>> references[("demo.py", 2)][0].target.line
    1
    ```

    """
    index = SymbolIndex(sources)
    return index.definitions, dict(index.references)
