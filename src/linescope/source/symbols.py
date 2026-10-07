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

    Attributes
    ----------
    CLASS : [_BindingKind]
        Represent a class definition or class lexical scope.

    FUNCTION : [_BindingKind]
        Represent a function definition or function scope.

    METHOD : [_BindingKind]
        Represent a method defined in a class body.

    MODULE : [_BindingKind]
        Represent a captured module and its lexical scope.

    UNKNOWN : [_BindingKind]
        Keep an unresolved or invalidated binding unavailable.

    INSTANCE : [_BindingKind]
        Represent the bound instance or class of a method.

    IMPORT : [_BindingKind]
        Represent an imported module or member binding.

    ALIAS : [_BindingKind]
        Represent an assigned expression requiring resolution.

    NAMESPACE : [_BindingKind]
        Represent an import namespace with captured submodules.

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

    Attributes
    ----------
    GLOBAL : [_RedirectKind]
        Redirect assignments to the containing module scope.

    NONLOCAL : [_RedirectKind]
        Redirect assignments to an enclosing function scope.

    """

    GLOBAL = "global"
    NONLOCAL = "nonlocal"


@dataclass(eq=False)
class _Scope:
    """Record lexical bindings and assignment redirects for one scope.

    Attributes
    ----------
    unit : [SourceUnit]
        Snapshot containing this lexical scope.

    kind : _BindingKind
        Scope category used by conservative name resolution.

    parent : _Scope | None
        Enclosing lexical scope, or None for a module.

    qualified_name : str
        Definition name including enclosing lexical scopes.

    bindings : dict[str, list[_Binding]]
        Candidate bindings grouped by identifier; duplicates are ambiguous.

    redirects : set[str]
        Names declared global or nonlocal in this scope.

    redirect_kinds : dict[str, _RedirectKind]
        Assignment destination category for each redirected identifier.

    definition : [SymbolDefinition] | None
        Navigable definition associated with this scope, when present.

    dynamic_class : bool
        Whether decorators or a metaclass make class lookup unsafe to infer.

    """

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
    """Represent one candidate lexical name binding.

    Attributes
    ----------
    kind : _BindingKind
        Definition, import, alias, instance, or unresolved binding category.

    scope : _Scope
        Lexical scope used to interpret this binding.

    definition : [SymbolDefinition] | None
        Resolved project definition, when available.

    expression : ast.expr | None
        Assigned expression retained for conservative alias resolution.

    module : str
        Imported module name, or an empty string for other bindings.

    member : str | None
        Imported member name, when this is a `from` import.

    level : int
        Relative import depth; zero denotes an absolute import.

    """

    kind: _BindingKind
    scope: _Scope
    definition: SymbolDefinition | None = None
    expression: ast.expr | None = None
    module: str = ""
    member: str | None = None
    level: int = 0


@dataclass
class _Target:
    """Carry a conservatively resolved call navigation target.

    Attributes
    ----------
    kind : _BindingKind
        Resolved target category used during call navigation.

    scope : _Scope
        Lexical scope owning the target or imported namespace.

    definition : [SymbolDefinition] | None
        Navigable project definition, when one can be resolved.

    module_name : str
        Namespace module prefix used to resolve imported submodules.

    """

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
    """Normalize a notebook snapshot identifier to its workspace path.

    Remove cell suffixes so related cells can share conservative navigation
    context.

    Parameters
    ----------
    unit : [SourceUnit]
        Captured source snapshot being inspected.

    Returns
    -------
    str
        Notebook workspace path without snapshot cell suffixes.

    """
    path = unit.id if unit.id.startswith("notebook://") else unit.path.split(" · cell ", 1)[0]
    return path.split("#", 1)[0].removeprefix("notebook://").rstrip("/")


class _Collector(ast.NodeVisitor):
    """Collect lexical bindings without running imports or user code.

    Record lexical scope and binding changes for conservative navigation.

    Attributes
    ----------
    scope : _Scope
        Current lexical scope while visiting the source tree.

    root : _Scope
        Module scope owning all bindings in this source snapshot.

    scopes : dict[ast.AST, _Scope]
        Lexical scope recorded for each visited syntax node.

    definitions : list[[SymbolDefinition]]
        Shared output list receiving navigable project definitions.

    assigned_attributes : set[str]
        Attribute names whose mutation prevents reliable static navigation.

    visit_FunctionDef : Callable[[ast.FunctionDef], None]
        Visitor alias collecting synchronous function definitions.

    visit_AsyncFunctionDef : Callable[[ast.AsyncFunctionDef], None]
        Visitor alias collecting asynchronous function definitions.

    visit_ClassDef : Callable[[ast.ClassDef], None]
        Visitor alias collecting class definitions and member scopes.

    visit_ListComp : Callable[[ast.ListComp], None]
        Visitor alias tracking list-comprehension scope.

    visit_SetComp : Callable[[ast.SetComp], None]
        Visitor alias tracking set-comprehension scope.

    visit_DictComp : Callable[[ast.DictComp], None]
        Visitor alias tracking dictionary-comprehension scope.

    visit_GeneratorExp : Callable[[ast.GeneratorExp], None]
        Visitor alias tracking generator-expression scope.

    """

    def __init__(self, unit: SourceUnit, definitions: list[SymbolDefinition]):
        """Initialize lexical collection for one captured source unit.

        Append definitions to the shared output without executing source or
        imports.

        Parameters
        ----------
        unit : [SourceUnit]
            Captured source snapshot being inspected.

        definitions : list[SymbolDefinition]
            Shared output list receiving resolved project definitions.

        """
        self.scope = _Scope(unit, _BindingKind.MODULE)
        self.root = self.scope
        self.scopes: dict[ast.AST, _Scope] = {}
        self.definitions = definitions
        self.assigned_attributes: set[str] = set()

    def visit(self, node: ast.AST):
        """Record a syntax node's current scope before dispatching its visitor.

        Retain scope ownership for later conservative expression resolution.

        Parameters
        ----------
        node : ast.AST
            Syntax or plan node being visited or resolved.

        """
        self.scopes[node] = self.scope
        super().visit(node)

    def _bind(self, name: str, binding: _Binding | None = None):
        """Add a candidate name binding to the current lexical scope.

        Treat unspecified bindings as unresolved rather than guessing their
        target.

        Parameters
        ----------
        name : str
            Identifier, method name, or binding label being inspected.

        binding : _Binding | None, default=None
            Candidate binding to record or resolve.

        """
        self.scope.bindings[name].append(binding or _Binding(_BindingKind.UNKNOWN, self.scope))

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        """Collect a function, method, or class definition and its inner scope.

        Preserve lexical names and flag classes whose dynamic behavior prevents
        safe lookup.

        Parameters
        ----------
        node : ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            Syntax or plan node being visited or resolved.

        """
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

    def _parameters(self, args: ast.arguments):
        """Register function parameters as unresolved lexical bindings.

        Include positional, keyword-only, variadic, and keyword capture
        parameters.

        Parameters
        ----------
        args : ast.arguments
            Positional arguments or syntax parameters forwarded to the
            operation.

        """
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            self._bind(arg.arg)

        for arg in (args.vararg, args.kwarg):
            if arg:
                self._bind(arg.arg)

    def visit_Lambda(self, node: ast.Lambda):
        """Collect a lambda's parameters and expression in a separate scope.

        Evaluate default expressions in the enclosing lexical scope.

        Parameters
        ----------
        node : ast.Lambda
            Syntax or plan node being visited or resolved.

        """
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
    ):
        """Collect bindings in a comprehension's own lexical scope.

        Keep the first iterable in the enclosing scope as required by Python
        semantics.

        Parameters
        ----------
        node : ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
            Syntax or plan node being visited or resolved.

        """
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

    def visit_Import(self, node: ast.Import):
        """Register module imports without executing their code.

        Preserve alias and top-level package binding behavior.

        Parameters
        ----------
        node : ast.Import
            Syntax or plan node being visited or resolved.

        """
        for alias in node.names:
            module = alias.name if alias.asname else alias.name.split(".")[0]
            self._bind(
                alias.asname or module, _Binding(_BindingKind.IMPORT, self.scope, module=module)
            )

    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Register imported members and relative module depth.

        Leave star imports unresolved rather than inferring their exported
        names.

        Parameters
        ----------
        node : ast.ImportFrom
            Syntax or plan node being visited or resolved.

        """
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

    def _assignment(self, target: ast.expr, value: ast.expr | None):
        """Record an assigned expression as a candidate alias binding.

        Visit non-name targets so attribute mutation remains visible to
        resolution.

        Parameters
        ----------
        target : ast.expr
            Assignment target or import reload target being inspected.

        value : ast.expr | None
            Measurement or serialized value to normalize or display.

        """
        if isinstance(target, ast.Name):
            self._bind(target.id, _Binding(_BindingKind.ALIAS, self.scope, expression=value))
        else:
            self.visit(target)

    def visit_Assign(self, node: ast.Assign):
        """Collect ordinary assignment targets and their value expression.

        Retain all candidate bindings so rebinding can invalidate inferred
        links.

        Parameters
        ----------
        node : ast.Assign
            Syntax or plan node being visited or resolved.

        """
        for target in node.targets:
            self._assignment(target, node.value)

        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        """Collect an annotated assignment and its annotation expression.

        Preserve unresolved targets when no assigned value is present.

        Parameters
        ----------
        node : ast.AnnAssign
            Syntax or plan node being visited or resolved.

        """
        self._assignment(node.target, node.value)

        if node.value:
            self.visit(node.value)

        self.visit(node.annotation)

    def visit_NamedExpr(self, node: ast.NamedExpr):
        """Collect a named expression's binding and value.

        Use the current lexical scope for conservative alias resolution.

        Parameters
        ----------
        node : ast.NamedExpr
            Syntax or plan node being visited or resolved.

        """
        self._assignment(node.target, node.value)
        self.visit(node.value)

    def visit_Name(self, node: ast.Name):
        """Register stored or deleted names as unresolved lexical bindings.

        Leave name reads available for the later resolution pass.

        Parameters
        ----------
        node : ast.Name
            Syntax or plan node being visited or resolved.

        """
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind(node.id)

    def visit_Attribute(self, node: ast.Attribute):
        """Record mutated attributes and inspect their owner expression.

        Prevent navigation through names that may be replaced dynamically.

        Parameters
        ----------
        node : ast.Attribute
            Syntax or plan node being visited or resolved.

        """
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.assigned_attributes.add(node.attr)

        self.visit(node.value)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        """Register an exception handler's bound name and visit its body.

        Treat the exception value as unresolved source state.

        Parameters
        ----------
        node : ast.ExceptHandler
            Syntax or plan node being visited or resolved.

        """
        if node.name:
            self._bind(node.name)

        self.generic_visit(node)

    def visit_Global(self, node: ast.Global):
        # Assignment through global/nonlocal is dynamic across calls. We do
        # not infer links through these declarations.
        """Record names whose assignments redirect to the module scope.

        Keep redirected identifiers out of speculative call navigation.

        Parameters
        ----------
        node : ast.Global
            Syntax or plan node being visited or resolved.

        """
        self.scope.redirects.update(node.names)
        self.scope.redirect_kinds.update(dict.fromkeys(node.names, _RedirectKind.GLOBAL))

    def visit_Nonlocal(self, node: ast.Nonlocal):
        """Record names whose assignments redirect to an enclosing scope.

        Retain redirection kinds for the mutation invalidation pass.

        Parameters
        ----------
        node : ast.Nonlocal
            Syntax or plan node being visited or resolved.

        """
        self.scope.redirects.update(node.names)
        self.scope.redirect_kinds.update(dict.fromkeys(node.names, _RedirectKind.NONLOCAL))

    def invalidate_mutations(self):
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

    def visit_MatchAs(self, node: ast.MatchAs):
        """Collect an alias introduced by structural pattern matching.

        Visit nested patterns to retain all potential lexical bindings.

        Parameters
        ----------
        node : ast.MatchAs
            Syntax or plan node being visited or resolved.

        """
        if node.name:
            self._bind(node.name)

        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar):
        """Collect the name bound by a starred sequence pattern.

        Leave the matched value unresolved for static navigation.

        Parameters
        ----------
        node : ast.MatchStar
            Syntax or plan node being visited or resolved.

        """
        if node.name:
            self._bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping):
        """Collect a mapping pattern's rest binding and nested patterns.

        Preserve unresolved pattern values without guessing definitions.

        Parameters
        ----------
        node : ast.MatchMapping
            Syntax or plan node being visited or resolved.

        """
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
    sources : dict[str, [SourceUnit]]
        Copy of the captured project files and notebook cells being indexed.

    definitions : list[[SymbolDefinition]]
        Lexical project definitions, including nested functions and methods.

    references : dict[tuple[str, int], list[[SymbolRef]]]
        Individually clickable call tokens grouped by source and line.

    _collectors : dict[str, _Collector]
        Per-snapshot binding collectors used for conservative resolution.

    _trees : dict[str, ast.Module]
        Successfully parsed syntax trees keyed by snapshot identifier.

    _modules : dict[str, list[_Scope]]
        Candidate module scopes grouped by importable name.

    _paths : dict[str, _Scope]
        Normalized file paths mapped to their module scopes.

    _assigned_attributes : set[str]
        Mutated attribute names excluded from inferred navigation.

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

    def __init__(self, sources: dict[str, SourceUnit]):
        """Index definitions and references from captured source.

        Resolve only unique project targets and keep dynamic or ambiguous calls
        unlinked.

        Parameters
        ----------
        sources : dict[str, SourceUnit]
            Captured source snapshots keyed by stable identifiers.

        """
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

    def _collect(self, unit: SourceUnit):
        """Parse a snapshot and collect its definitions and lexical bindings.

        Retain notebook line positions while masking magic syntax and skip
        invalid source.

        Parameters
        ----------
        unit : [SourceUnit]
            Captured source snapshot being inspected.

        """
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
        """Resolve an imported module to one unique captured module scope.

        Return None when project snapshots are absent or candidates are
        ambiguous.

        Parameters
        ----------
        name : str
            Identifier, method name, or binding label being inspected.

        scope : _Scope
            Lexical scope used for conservative resolution.

        level : int, default=0
            Relative import depth; zero represents an absolute import.

        Returns
        -------
        _Scope | None
            Unique captured module scope, or None when unresolved.

        """
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
        """Find captured notebook cell scopes sharing inline execution context.

        Restrict peer lookup to the current notebook and resolvable inline
        imports.

        Parameters
        ----------
        scope : _Scope
            Lexical scope used for conservative resolution.

        Returns
        -------
        list[_Scope]
            Notebook module scopes sharing the relevant execution context.

        """
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
        """Resolve a lexical identifier without following ambiguous bindings.

        Guard alias cycles and skip class locals when resolving function
        closures.

        Parameters
        ----------
        name : str
            Identifier, method name, or binding label being inspected.

        scope : _Scope
            Lexical scope used for conservative resolution.

        seen : set[tuple[int, str]]
            Already visited identities used to prevent resolution cycles.

        Returns
        -------
        _Target | None
            Unique navigation target, or None when unresolved or ambiguous.

        """
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
        """Resolve a definition, alias, or import binding to a project target.

        Keep external and missing targets unavailable instead of executing
        imports.

        Parameters
        ----------
        binding : _Binding
            Candidate binding to record or resolve.

        seen : set[tuple[int, str]]
            Already visited identities used to prevent resolution cycles.

        Returns
        -------
        _Target | None
            Resolved binding target, or None when unavailable.

        """
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
        """Check whether captured modules exist below a namespace prefix.

        Use only the already collected project module index.

        Parameters
        ----------
        name : str
            Identifier, method name, or binding label being inspected.

        Returns
        -------
        bool
            Whether any captured module belongs to the namespace.

        """
        return any(candidate.startswith(f"{name}.") for candidate in self._modules)

    def _attribute(self, owner: _Target, attr: str, seen: set[tuple[int, str]]) -> _Target | None:
        """Resolve a stable attribute on a known project owner.

        Reject mutated names, ambiguous definitions, and dynamic class dispatch.

        Parameters
        ----------
        owner : _Target
            Conservatively resolved owner of the requested attribute.

        attr : str
            Attribute name being resolved.

        seen : set[tuple[int, str]]
            Already visited identities used to prevent resolution cycles.

        Returns
        -------
        _Target | None
            Stable project target, or None when inference is unsafe.

        """
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
        """Resolve a supported name, attribute, or constructor expression.

        Keep unsupported expression forms unavailable for source navigation.

        Parameters
        ----------
        node : ast.expr
            Syntax or plan node being visited or resolved.

        scope : _Scope
            Lexical scope used for conservative resolution.

        seen : set[tuple[int, str]]
            Already visited identities used to prevent resolution cycles.

        Returns
        -------
        _Target | None
            Resolved expression target, or None for unsupported forms.

        """
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

    def _link(self, source_id: str, tree: ast.Module):
        """Collect resolvable call and notebook references from a snapshot.

        Link individual source tokens and preserve Unicode character offsets.

        Parameters
        ----------
        source_id : str
            Stable identifier of the captured source snapshot.

        tree : ast.Module
            Parsed module syntax tree for a captured snapshot.

        """
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

    def _link_notebook(self, unit: SourceUnit, path: str, line: int, start: int, end: int):
        """Link an inline notebook token to one captured notebook target.

        Omit links when the workspace path cannot be resolved conservatively.

        Parameters
        ----------
        unit : [SourceUnit]
            Captured source snapshot being inspected.

        path : str
            File, workspace, or import search path used by this operation.

        line : int
            One-based source line used by the report link.

        start : int
            Inclusive source token character offset.

        end : int
            Exclusive source token character offset.

        """
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
