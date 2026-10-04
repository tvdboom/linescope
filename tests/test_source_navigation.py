"""LineScope.

Author: Mavs
Description: Token-level symbol resolution and conservative navigation tests.

"""

import textwrap

import pytest

from linescope.model import SourceUnit
from linescope.source import SymbolIndex, build_navigation


def make_source(text, path="/project/main.py", kind="python"):
    """Provide make source.

    Create a source snapshot without touching the filesystem.

    """
    return SourceUnit(path, path, textwrap.dedent(text).lstrip("\n"), kind=kind)


def references(*units):
    """Provide references.

    Flatten an index into tokens for easy assertions.

    """
    index = SymbolIndex({unit.id: unit for unit in units})
    return [reference for values in index.references.values() for reference in values]


class TestSymbolDefinitions:
    """Check symbol definitions.

    Tests for complete lexical source indexes.

    """

    def test_functions_classes_methods_and_nested_functions(self):
        """Check functions classes methods and nested functions.

        All supported definition kinds retain their lexical location.

        """
        unit = make_source("""
            class Processor:
                def run(self):
                    def inner():
                        return 1
                    return inner()
            async def fetch():
                return 2
        """)
        definitions, _ = build_navigation({unit.id: unit})
        assert [(item.kind, item.qualified_name, item.line) for item in definitions] == [
            ("class", "Processor", 1),
            ("method", "Processor.run", 2),
            ("function", "Processor.run.inner", 3),
            ("function", "fetch", 6),
        ]

    @pytest.mark.parametrize("text", ["def broken(:", "\x00", "%%bash\necho hi"])
    def test_unparseable_source_stays_unlinked(self, text):
        """Check unparseable source stays unlinked.

        Invalid or non-Python source never prevents report generation.

        """
        unit = make_source(text)
        assert build_navigation({unit.id: unit}) == ([], {})


class TestCallNavigation:
    """Check call navigation.

    Tests for exact call-token navigation.

    """

    def test_multiple_calls_per_line(self):
        """Check multiple calls per line.

        Nested calls yield separate non-overlapping links.

        """
        unit = make_source(
            "def foo(value): return value\ndef bar(value): return value\nresult = foo(bar(1))\n"
        )
        refs = references(unit)
        assert [(ref.name, ref.column, ref.end_column, ref.target.line) for ref in refs] == [
            ("foo", 9, 12, 1),
            ("bar", 13, 16, 2),
        ]

    def test_unicode_character_offsets(self):
        """Check unicode character offsets.

        UTF-8 AST byte positions become correct character offsets.

        """
        unit = make_source("def café(): return 1\nresult = '🍋'; café()\n")
        ref = references(unit)[0]
        assert ref.column == 14
        assert unit.source.splitlines()[ref.line - 1][ref.column : ref.end_column] == "café"

    def test_inferred_instance_method_and_constructor(self):
        """Check inferred instance method and constructor.

        A single constructor assignment resolves a project method.

        """
        unit = make_source("""
            class Processor:
                def transform(self, value): return value
            processor = Processor()
            result = processor.transform(1)
        """)
        refs = references(unit)
        assert [(ref.name, ref.target.line) for ref in refs] == [
            ("Processor", 1),
            ("transform", 2),
        ]
        assert refs[1].column == 19

    def test_self_and_class_method(self):
        """Check self and class method.

        Ordinary self references and class calls resolve direct methods.

        """
        unit = make_source("""
            class Processor:
                def transform(self): return 1
                def run(self): return self.transform()
            Processor.transform(None)
        """)
        refs = references(unit)
        assert len(refs) == 2
        assert all(ref.target.symbol == "Processor.transform" for ref in refs)

    def test_immediate_constructed_method(self):
        """Check immediate constructed method.

        Methods on directly constructed instances resolve both tokens.

        """
        unit = make_source("class Worker:\n    def run(self): pass\nWorker().run()\n")
        assert {ref.name for ref in references(unit)} == {"Worker", "run"}

    def test_aliases_and_nested_local_functions(self):
        """Check aliases and nested local functions.

        Single-assignment aliases and lexical nested calls are reliable.

        """
        unit = make_source("""
            def outer():
                def inner(): return 1
                alias = inner
                return alias()
            outer()
        """)
        assert {(ref.name, ref.target.symbol) for ref in references(unit)} == {
            ("alias", "outer.inner"),
            ("outer", "outer"),
        }

    def test_multiline_method_call(self):
        """Check multiline method call.

        Attribute tokens on a continuation line use that line's columns.

        """
        unit = make_source(
            "class Worker:\n    def run(self): pass\nworker = Worker()\n(worker\n .run)()\n"
        )
        refs = [ref for ref in references(unit) if ref.name == "run"]
        assert [(ref.line, ref.column, ref.end_column) for ref in refs] == [(5, 2, 5)]

    def test_normalized_identifier_spelling(self):
        """Check normalized identifier spelling.

        Unicode normalization never changes the span in original source.

        """
        unit = make_source("class Worker:\n    def ffi(self): pass\nWorker().ﬃ()\n")
        ref = next(ref for ref in references(unit) if ref.target.symbol == "Worker.ffi")
        assert (ref.name, ref.column, ref.end_column) == ("ﬃ", 9, 10)

    @pytest.mark.parametrize(
        "statement", ["fn = other", "def fn(): pass", "for fn in items: pass", "del fn"]
    )
    def test_rebinding_omits_ambiguous_links(self, statement):
        """Check rebinding omits ambiguous links.

        Names with multiple potential bindings are never guessed.

        """
        unit = make_source(f"def fn(): return 1\n{statement}\nfn()\n")
        assert references(unit) == []

    def test_unknown_parameter_shadows_global(self):
        """Check unknown parameter shadows global.

        A same-named function parameter blocks a misleading global link.

        """
        unit = make_source("def fn(): pass\ndef work(fn):\n    fn()\n")
        assert references(unit) == []

    def test_comprehension_scope(self):
        """Check comprehension scope.

        Comprehension target names shadow globals only inside the expression.

        """
        unit = make_source("def fn(): pass\nvalues = [fn() for fn in functions]\nfn()\n")
        assert [(ref.name, ref.line) for ref in references(unit)] == [("fn", 3)]

    def test_lambda_scope(self):
        """Check lambda scope.

        Lambda parameters do not resolve to project global definitions.

        """
        unit = make_source("def fn(): pass\nclosure = lambda fn: fn()\n")
        assert references(unit) == []

    def test_class_scope_not_closed_over_by_methods(self):
        """Check class scope not closed over by methods.

        Unqualified identifiers in methods skip class lexical names.

        """
        unit = make_source(
            "class Worker:\n    def helper(self): pass\n    def run(self): helper()\n"
        )
        assert references(unit) == []

    def test_staticmethod_parameter_not_instance(self):
        """Check staticmethod parameter not instance.

        A static method's first argument has no inferred class type.

        """
        unit = make_source("""
            class Worker:
                def helper(self): pass
                @staticmethod
                def run(obj): obj.helper()
        """)
        assert references(unit) == []

    def test_dynamic_and_monkeypatched_methods_not_linked(self):
        """Check dynamic and monkeypatched methods not linked.

        Dynamic attribute hooks and explicit method mutation prevent links.

        """
        dynamic = make_source(
            "class Worker:\n    def __getattr__(self, name): pass\n    def run(self):"
            " pass\nWorker().run()\n"
        )
        assert "run" not in {ref.name for ref in references(dynamic)}
        patched = make_source(
            "class Worker:\n    def run(self): pass\nworker = Worker()\nworker.run ="
            " external\nworker.run()\n"
        )
        assert "run" not in {ref.name for ref in references(patched)}

    def test_alias_cycle(self):
        """Check alias cycle.

        Cyclic alias assignments terminate without speculative links.

        """
        unit = make_source("first = second\nsecond = first\nfirst()\n")
        assert references(unit) == []

    def test_global_replacement(self):
        """Check global replacement.

        A mutation in another function makes the global binding unsafe.

        """
        unit = make_source(
            "def fn(): pass\ndef replace():\n    global fn\n    fn = external\nfn()\n"
        )
        assert references(unit) == []

    def test_nonlocal_replacement(self):
        """Check nonlocal replacement.

        A nested function may mutate its enclosing callable binding.

        """
        unit = make_source(
            "def outer():\n    def fn(): pass\n    def replace():\n        nonlocal fn\n  "
            "      fn = external\n    fn()\n"
        )
        assert references(unit) == []

    @pytest.mark.parametrize(
        "declaration", ["@replace\nclass Worker:", "class Worker(metaclass=Factory):"]
    )
    def test_dynamic_class_creation(self, declaration):
        """Check dynamic class creation.

        Decorators and custom metaclasses block instance type inference.

        """
        unit = make_source(f"{declaration}\n    def run(self): pass\nWorker().run()\n")
        assert "run" not in {ref.name for ref in references(unit)}

    def test_factory_new(self):
        """Check factory new.

        Custom object allocation can return an unrelated runtime type.

        """
        unit = make_source(
            "class Worker:\n    def __new__(cls): return external\n    def run(self):"
            " pass\nWorker().run()\n"
        )
        assert "run" not in {ref.name for ref in references(unit)}

    def test_third_party_stays_unlinked(self):
        """Check third party stays unlinked.

        Unsnapshotted third-party imports never become navigation targets.

        """
        unit = make_source(
            "import pandas as pd\nfrom numpy import mean\npd.read_parquet('data')\nmean([1])\n"
        )
        assert references(unit) == []


class TestImportedNavigation:
    """Check imported navigation.

    Tests for aliases, relative imports, and ambiguous package names.

    """

    @pytest.mark.parametrize(
        ("statement", "call"),
        [
            ("from package.worker import run", "run()"),
            ("from package.worker import run as work", "work()"),
            ("import package.worker as worker", "worker.run()"),
            ("from .worker import run", "run()"),
        ],
    )
    def test_imports(self, statement, call):
        """Check imports.

        Supported imports navigate to the captured project definition.

        """
        caller = make_source(f"{statement}\n{call}\n", "/project/src/package/main.py")
        target = make_source("def run(): pass\n", "/project/src/package/worker.py")
        ref = references(caller, target)[0]
        assert ref.target.source_id == target.id
        assert ref.target.line == 1

    def test_qualified_package_import(self):
        """Check qualified package import.

        Full package chains resolve through captured package initializers.

        """
        init = make_source("", "/project/package/__init__.py")
        worker = make_source("def run(): pass", "/project/package/worker.py")
        caller = make_source("import package.worker\npackage.worker.run()\n")
        assert references(init, worker, caller)[0].target.source_id == worker.id

    def test_uncaptured_package_initializer(self):
        """Check uncaptured package initializer.

        Package imports work when only executed module source was captured.

        """
        worker = make_source("def run(): pass", "/project/package/worker.py")
        caller = make_source("import package.worker\npackage.worker.run()\n")
        assert references(worker, caller)[0].target.source_id == worker.id

    def test_reexport(self):
        """Check reexport.

        A project package can reexport a function with a relative import.

        """
        init = make_source("from .worker import run", "/project/package/__init__.py")
        worker = make_source("def run(): pass", "/project/package/worker.py")
        caller = make_source("from package import run\nrun()\n")
        assert references(init, worker, caller)[0].target.source_id == worker.id

    def test_relative_parent_import(self):
        """Check relative parent import.

        Relative imports correctly ascend package levels.

        """
        worker = make_source("def run(): pass", "/project/package/worker.py")
        caller = make_source("from ..worker import run\nrun()\n", "/project/package/sub/main.py")
        assert references(worker, caller)[0].target.source_id == worker.id

    def test_ambiguous_modules_not_linked(self):
        """Check ambiguous modules not linked.

        Duplicate suffix module names do not produce guessed links.

        """
        first = make_source("def run(): pass", "/first/package/worker.py")
        second = make_source("def run(): pass", "/second/package/worker.py")
        caller = make_source("from package.worker import run\nrun()\n")
        assert references(first, second, caller) == []

    def test_imported_class_instance(self):
        """Check imported class instance.

        Imported class aliases retain direct instance method navigation.

        """
        worker = make_source("class Worker:\n    def run(self): pass\n", "/project/worker.py")
        caller = make_source("from worker import Worker as Job\njob = Job()\njob.run()\n")
        assert [(ref.name, ref.target.line) for ref in references(worker, caller)] == [
            ("Job", 1),
            ("run", 2),
        ]


class TestNotebookNavigation:
    """Check notebook navigation.

    Tests for notebook cells sharing a kernel namespace.

    """

    def test_cross_cell_function(self):
        """Check cross cell function.

        Functions defined in an earlier captured cell remain navigable.

        """
        first = make_source("def clean(): pass", "notebook://project/main#cell-1", "notebook")
        second = make_source("%%profile\nclean()", "notebook://project/main#cell-2", "notebook")
        assert references(first, second)[0].target.source_id == first.id

    def test_human_readable_cell_paths(self):
        """Check human readable cell paths.

        Cell labels do not split a real notebook's shared namespace.

        """
        first = SourceUnit(
            "notebook:///Workspace/main#cell-1-abc",
            "/Workspace/main · cell 1",
            "def clean(): pass",
            "notebook",
        )
        second = SourceUnit(
            "notebook:///Workspace/main#cell-2-def",
            "/Workspace/main · cell 2",
            "clean()",
            "notebook",
        )
        assert references(first, second)[0].target.source_id == first.id

    def test_magic_keeps_line_numbers(self):
        """Check magic keeps line numbers.

        Removing magic syntax from analysis preserves report positions.

        """
        unit = make_source(
            "%%profile\ndef clean(): pass\nclean()", "notebook://main#cell-1", "notebook"
        )
        ref = references(unit)[0]
        assert (ref.line, ref.target.line) == (3, 2)

    def test_run_imported_notebook_definition(self):
        """Check run imported notebook definition.

        A resolvable percent-run makes child notebook functions visible.

        """
        common = make_source(
            "def clean(): pass", "notebook:///Workspace/project/common#cell-1", "notebook"
        )
        main = make_source(
            "%run ./common\nclean()", "notebook:///Workspace/project/main#cell-1", "notebook"
        )
        assert all(ref.target.source_id == common.id for ref in references(common, main))

    def test_run_path_token(self):
        """Check run path token.

        Percent-run links the notebook path without consuming the whole line.

        """
        common = make_source(
            "def clean(): pass", "notebook:///Workspace/project/common#cell-1", "notebook"
        )
        main = make_source(
            '%run "./common" $arg="value"', "notebook:///Workspace/project/main#cell-1", "notebook"
        )
        ref = references(common, main)[0]
        assert (ref.name, ref.column, ref.end_column) == ("./common", 6, 14)
        assert ref.target.source_id == common.id

    @pytest.mark.parametrize(
        "argument", ['"./child", 3600', 'path="./child", timeout_seconds=3600']
    )
    def test_dbutils_path_token(self, argument):
        """Check dbutils path token.

        Literal notebook.run paths resolve to captured child source.

        """
        child = make_source(
            "# Child source unavailable.", "notebook:///Workspace/project/child", "notebook"
        )
        main = make_source(
            f"dbutils.notebook.run({argument})",
            "notebook:///Workspace/project/main#cell-1",
            "notebook",
        )
        ref = references(child, main)[0]
        assert ref.target.source_id == child.id
        assert main.source[ref.column : ref.end_column] == '"./child"'

    def test_dynamic_notebook_path_unlinked(self):
        """Check dynamic notebook path unlinked.

        Expressions in notebook paths require runtime correlation evidence.

        """
        child = make_source(
            "# Child source unavailable.", "notebook:///Workspace/project/child", "notebook"
        )
        main = make_source(
            "dbutils.notebook.run(path, 3600)",
            "notebook:///Workspace/project/main#cell-1",
            "notebook",
        )
        assert references(child, main) == []

    def test_unrelated_notebook_does_not_leak(self):
        """Check unrelated notebook does not leak.

        Separate notebook namespaces never supply accidental symbols.

        """
        first = make_source("def clean(): pass", "notebook://first#cell-1", "notebook")
        second = make_source("clean()", "notebook://second#cell-1", "notebook")
        assert references(first, second) == []

    def test_redefined_cells_ambiguous(self):
        """Check redefined cells ambiguous.

        Repeated notebook definitions require runtime evidence to link.

        """
        first = make_source("def clean(): pass", "notebook://main#cell-1", "notebook")
        second = make_source("def clean(): pass\nclean()", "notebook://main#cell-2", "notebook")
        assert references(first, second) == []
