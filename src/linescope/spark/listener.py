"""LineScope.

Author: Mavs
Description: Reversible Spark action observation with optional JVM query
listeners.

"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
import functools
import importlib
from importlib.abc import Loader
from importlib.machinery import ModuleSpec
import sys
import threading
from time import perf_counter_ns, time_ns
from types import ModuleType
from typing import Any, ClassVar, TypeVar
from uuid import uuid4
import weakref

from linescope.enums import RunStatus
from linescope.model import SourceLocation, SparkExecution, SparkExecutionStats
from linescope.notebooks.databricks import caller_location
from linescope.spark.metrics import _execution_executor_time, optional_call, stage_statistics
from linescope.spark.plans import capture_query

_T = TypeVar("_T")
_LOCK = threading.RLock()
_ACTIVE: dict[int, list[SparkIntegration]] = {}
_PATCHES: list[tuple[type, str, Any, Any, bool]] = []
_IMPORT_WATCHER: _SparkFinder | None = None
_ACTIONS = {
    "collect",
    "count",
    "show",
    "take",
    "head",
    "first",
    "tail",
    "toPandas",
    "toArrow",
    "foreach",
    "foreachPartition",
    "isEmpty",
}
_TRANSFORMS = {
    "filter",
    "where",
    "select",
    "selectExpr",
    "withColumn",
    "withColumns",
    "withColumnRenamed",
    "drop",
    "dropDuplicates",
    "distinct",
    "join",
    "crossJoin",
    "union",
    "unionAll",
    "unionByName",
    "intersect",
    "subtract",
    "groupBy",
    "groupby",
    "rollup",
    "cube",
    "agg",
    "orderBy",
    "sort",
    "sortWithinPartitions",
    "limit",
    "repartition",
    "coalesce",
    "sample",
    "alias",
    "cache",
    "persist",
    "unpersist",
    "hint",
    "checkpoint",
    "localCheckpoint",
    "writeTo",
}
_WRITES = {
    "save",
    "saveAsTable",
    "insertInto",
    "parquet",
    "json",
    "csv",
    "orc",
    "text",
    "jdbc",
    "create",
    "replace",
    "createOrReplace",
    "append",
    "overwrite",
    "overwritePartitions",
}


class _MethodKind(StrEnum):
    """Classify intercepted Spark methods by their observation behavior.

    Transformations record lineage while actions and writes execute the
    user's original callback.

    Attributes
    ----------
    ACTION : [_MethodKind]
        Observe an action that triggers Spark execution.

    WRITE : [_MethodKind]
        Observe a writer action through its owning DataFrame.

    TRANSFORM : [_MethodKind]
        Record lazy DataFrame lineage without triggering work.

    """

    ACTION = "action"
    WRITE = "write"
    TRANSFORM = "transform"


def _current() -> SparkIntegration | None:
    """Return the active Spark observer for the calling thread.

    Use the innermost observer when integrations are nested.

    Returns
    -------
    [SparkIntegration] | None
        Current owned observer or session, when available.

    """
    with _LOCK:
        stack = _ACTIVE.get(threading.get_ident(), [])
        return stack[-1] if stack else None


def _install_class(cls: type, names: set[str], kind: _MethodKind) -> None:
    """Wrap supported methods while retaining originals and patch ownership.

    Distinguish lazy transformations from actions and writer operations.

    Parameters
    ----------
    cls : type
        Class or model whose supported members are inspected.

    names : set[str]
        Supported method names eligible for observation.

    kind : _MethodKind
        Observation category distinguishing actions, writes, and transforms.

    """
    for name in sorted(names):
        original = getattr(cls, name, None)

        if not callable(original) or any(c is cls and n == name for c, n, *_ in _PATCHES):
            continue

        owned = name in vars(cls)

        def wrap(method: Any, method_name: str) -> Any:
            """Create an observer wrapper for one original Spark method.

            Capture its name without changing the method's arguments or result.

            Parameters
            ----------
            method : Any
                Original method or member selection used by the operation.

            method_name : str
                Original Spark method name retained in action metadata.

            Returns
            -------
            Any
                Wrapper forwarding calls through active Spark observation.

            """

            @functools.wraps(method)
            def observed(obj: Any, *args: Any, **kwargs: Any) -> Any:
                """Forward a Spark call through the current thread's observer.

                Record lazy lineage or one action while avoiding duplicate
                nested observations.

                Parameters
                ----------
                obj : Any
                    Object whose behavior is observed or documented.

                *args : Any
                    Positional arguments or syntax parameters forwarded to the
                    operation.

                **kwargs : Any
                    Keyword arguments forwarded without changing their values.

                Returns
                -------
                Any
                    Original Spark method result.

                """
                active = _current()

                if active is None and kind in {_MethodKind.ACTION, _MethodKind.WRITE}:
                    with _LOCK:
                        for stack in _ACTIVE.values():
                            for observer in stack:
                                observer._concurrent_queries = True

                if active is None or active._depth:
                    return method(obj, *args, **kwargs)

                if kind == _MethodKind.ACTION:
                    return active.record_action(
                        obj, method_name, lambda: method(obj, *args, **kwargs)
                    )

                if kind == _MethodKind.WRITE:
                    frame = getattr(obj, "_df", None)

                    if frame is None:
                        frame = active._owners.get(id(obj), obj)

                    return active.record_action(
                        frame, f"write.{method_name}", lambda: method(obj, *args, **kwargs)
                    )

                location = caller_location(active.session)
                active._depth += 1

                try:
                    result = method(obj, *args, **kwargs)
                finally:
                    active._depth -= 1

                try:
                    active._remember(result, obj, args, location)
                except Exception as error:  # noqa: BLE001
                    active._warning(error)

                return result

            return observed

        wrapper = wrap(original, name)
        setattr(cls, name, wrapper)
        _PATCHES.append((cls, name, original, wrapper, owned))


_SPECIFICATIONS = [
    ("pyspark.sql.dataframe", "DataFrame", _ACTIONS, _MethodKind.ACTION),
    ("pyspark.sql.dataframe", "DataFrame", _TRANSFORMS, _MethodKind.TRANSFORM),
    ("pyspark.sql.classic.dataframe", "DataFrame", _ACTIONS, _MethodKind.ACTION),
    ("pyspark.sql.classic.dataframe", "DataFrame", _TRANSFORMS, _MethodKind.TRANSFORM),
    ("pyspark.sql.readwriter", "DataFrameWriter", _WRITES, _MethodKind.WRITE),
    ("pyspark.sql.readwriter", "DataFrameWriterV2", _WRITES, _MethodKind.WRITE),
    (
        "pyspark.sql.readwriter",
        "DataFrameReader",
        {"load", "parquet", "json", "csv", "orc", "text", "jdbc", "table"},
        _MethodKind.TRANSFORM,
    ),
    (
        "pyspark.sql.group",
        "GroupedData",
        {"agg", "count", "sum", "min", "max", "avg", "mean", "pivot"},
        _MethodKind.TRANSFORM,
    ),
    (
        "pyspark.sql.session",
        "SparkSession",
        {"range", "createDataFrame", "sql", "table"},
        _MethodKind.TRANSFORM,
    ),
]
_SPARK_MODULES = frozenset(module_name for module_name, *_ in _SPECIFICATIONS)


def _install_patches() -> None:
    # Only touch modules the workload has loaded. Installing observation must
    # never turn an unused optional dependency into a PySpark import.
    """Wrap supported classes from modules already loaded by the workload.

    Leave unused PySpark modules unloaded.

    """
    for module_name, class_name, names, kind in _SPECIFICATIONS:
        module = sys.modules.get(module_name)
        cls = getattr(module, class_name, None)

        if isinstance(cls, type):
            _install_class(cls, names, kind)


class _SparkLoader(Loader):
    """Observe completed PySpark imports through their original loader.

    Attributes
    ----------
    original : Any
        Underlying import loader whose behavior and metadata are preserved.

    """

    def __init__(self, original: Any) -> None:
        """Retain the owner or original loader needed by this Spark adapter.

        Defer query listener registration and optional JVM access until an
        action runs.

        Parameters
        ----------
        original : Any
            Existing implementation retained for delegation and cleanup.

        """
        self.original = original

    def __getattr__(self, name: str) -> Any:
        """Forward unhandled loader attributes to the original implementation.

        Preserve import protocol behavior outside the owned execution observer.

        Parameters
        ----------
        name : str
            Identifier, method name, or binding label being inspected.

        Returns
        -------
        Any
            Original loader attribute value.

        """
        return getattr(self.original, name)

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        """Delegate module construction to the original loader when supported.

        Return None when Python should construct the module normally.

        Parameters
        ----------
        spec : ModuleSpec
            Import specification supplied by the existing finder.

        Returns
        -------
        ModuleType | None
            Original module instance, or None for default construction.

        """
        create = getattr(self.original, "create_module", None)
        return create(spec) if create is not None else None

    def exec_module(self, module: ModuleType) -> None:
        """Execute an imported module through its original loader.

        Restore loader metadata and record optional patch failures without
        replacing import errors.

        Parameters
        ----------
        module : ModuleType
            Imported module whose loader execution is observed.

        """
        try:
            self.original.exec_module(module)
        finally:
            # The proxy belongs to this import only; retain the module's
            # original loader metadata even when its execution fails.
            if module.__loader__ is self:
                module.__loader__ = self.original

            if module.__spec__ is not None and module.__spec__.loader is self:
                module.__spec__.loader = self.original

        with _LOCK:
            if not _ACTIVE:
                return

            try:
                _install_patches()
            except Exception as error:  # noqa: BLE001
                # Optional observation must preserve successful user imports.
                for stack in _ACTIVE.values():
                    for observer in stack:
                        observer._warning(error)


class _SparkFinder:
    """Wrap loaders only for PySpark modules requested by user code.

    Delegate discovery to existing finders and preserve import order.

    """

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        """Wrap the existing loader for a requested supported PySpark module.

        Preserve finder order and avoid loading modules the workload did not
        request.

        Parameters
        ----------
        fullname : str
            Fully qualified module name requested by user code.

        path : Any, default=None
            File, workspace, or import search path used by this operation.

        target : ModuleType | None, default=None
            Assignment target or import reload target being inspected.

        Returns
        -------
        ModuleSpec | None
            Existing import specification with an observed loader, if found.

        """
        if fullname not in _SPARK_MODULES:
            return None

        # Preserve the existing finder order and loader. The watcher never
        # searches for, or loads, a module the workload has not requested.
        for finder in tuple(sys.meta_path):
            if finder is self:
                continue

            spec = finder.find_spec(fullname, path, target)

            if spec is None:
                continue

            if spec.loader is not None and hasattr(spec.loader, "exec_module"):
                spec.loader = _SparkLoader(spec.loader)

            return spec

        return None


class _QueryListener:
    """Forward JVM query completion callbacks to the owning observer.

    Attributes
    ----------
    integration : [SparkIntegration]
        Owning observer receiving JVM query completion callbacks.

    """

    class Java:
        """Declare the JVM interfaces implemented by the query listener proxy.

        Attributes
        ----------
        implements : ClassVar[list[str]]
            JVM callback interfaces declared for the Py4J proxy.

        """

        implements: ClassVar[list[str]] = ["org.apache.spark.sql.util.QueryExecutionListener"]

    def __init__(self, integration: SparkIntegration) -> None:
        """Retain the owner or original loader needed by this Spark adapter.

        Defer query listener registration and optional JVM access until an
        action runs.

        Parameters
        ----------
        integration : [SparkIntegration]
            Owning observer receiving query completion callbacks.

        """
        self.integration = integration

    def onSuccess(self, name: str, query: Any, duration: int) -> None:
        """Forward a successful JVM query callback to the owning integration.

        Preserve Spark's query duration separately from driver action waiting.

        Parameters
        ----------
        name : str
            Identifier, method name, or binding label being inspected.

        query : Any
            JVM query execution supplying plan and completion metadata.

        duration : int
            Query duration reported by Spark, in nanoseconds.

        """
        self.integration._query_finished(str(name), query, int(duration), RunStatus.SUCCESS)

    def onFailure(self, name: str, query: Any, error: Any) -> None:
        """Forward failed JVM query callbacks without error details.

        Mark the action failed while keeping unavailable timing unknown.

        Parameters
        ----------
        name : str
            Identifier, method name, or binding label being inspected.

        query : Any
            JVM query execution supplying plan and completion metadata.

        error : Any
            Observed error; reports retain safe diagnostic information only.

        """
        del error
        self.integration._query_finished(str(name), query, None, RunStatus.FAILED)


class SparkIntegration:
    """Observe driver [DataFrame] actions without introducing Spark actions.

    Instrumentation applies to the Python thread that starts the adapter.
    Other threads and unrelated sessions continue through their original
    methods. A JVM query listener captures actual action plans when callback
    access is allowed. Restricted environments receive a clearly labeled
    input-plan fallback. Executor Python UDFs are outside this scope.

    Starting the adapter does not import PySpark or contact the JVM. Supported
    methods are wrapped as their modules become available, including modules
    loaded before profiling. The query listener starts only when an observed
    action runs.

    Parameters
    ----------
    session : [Session]
        Owning LineScope session.

    spark : [SparkSession] | None, default=None
        Existing Spark session. No Spark cluster/session is created by this
        adapter; an active session is detected when available.

    Attributes
    ----------
    session : [Session]
        Owning session receiving observed actions and collection diagnostics.

    spark : [SparkSession] | None
        Supplied or lazily detected active Spark session.

    _thread : int
        Identifier of the thread whose Spark calls are observed.

    _active : bool
        Whether this integration currently participates in method observation.

    _depth : int
        Nested action wrapper depth used to avoid duplicate observations.

    _lineage : dict[int, tuple[Any, list[[SourceLocation]]]]
        DataFrame identities and their project transformation locations.

    _owners : dict[int, Any]
        Writer identities mapped to their owning DataFrames.

    _pending : list[tuple[[SparkExecution], str | None]]
        Actions awaiting an unambiguous JVM query completion match.

    _listener : Any
        Registered Py4J query listener proxy, when available.

    _listener_manager : Any
        JVM listener manager used to unregister the owned callback.

    _listener_attempted : bool
        Whether query listener startup has already been attempted.

    _concurrent_queries : bool
        Whether overlapping callbacks make action correlation ambiguous.

    _lock : threading.RLock
        Lock protecting query completion and pending action state.

    See Also
    --------
    - linescope:Session
    - linescope.model:SparkExecution
    - linescope.spark:parse_plan

    Examples
    --------
    [Session] lifecycle normally installs the adapter automatically. Explicit
    construction is useful for integrations with another Spark frontend.

    ```pycon
    >>> from linescope import Session
    >>> from linescope.spark import SparkIntegration
    >>> session = Session(backend="trace", display="none")
    >>> integration = SparkIntegration(session)
    >>> integration.session is session
    True
    ```

    """

    def __init__(self, session: Any, spark: Any = None) -> None:
        """Retain the owner or original loader needed by this Spark adapter.

        Defer query listener registration and optional JVM access until an
        action runs.

        Parameters
        ----------
        session : Any
            Owning profiling session receiving measurements and diagnostics.

        spark : Any, default=None
            Existing Spark session, when available.

        """
        self.session = session
        self.spark: Any = spark
        self._thread = threading.get_ident()
        self._active = False
        self._depth = 0
        self._lineage: dict[int, tuple[Any, list[SourceLocation]]] = {}
        self._owners: dict[int, Any] = {}
        self._pending: list[tuple[SparkExecution, str | None]] = []
        self._listener: Any = None
        self._listener_manager: Any = None
        self._listener_attempted = False
        self._concurrent_queries = False
        self._lock = threading.RLock()

    def _warning(self, error: Exception) -> None:
        """Record one diagnostic for unavailable optional Spark metadata.

        Include the error type without leaking workload exception details.

        Parameters
        ----------
        error : Exception
            Observed error; reports retain safe diagnostic information only.

        """
        message = (
            f"Spark metadata unavailable ({type(error).__name__}); user execution was preserved."
        )

        if message not in self.session.result.warnings:
            self.session.result.warnings.append(message)

    def _detect_session(self) -> Any:
        """Return a supplied or already active Spark session when available.

        Inspect loaded PySpark modules without creating a session or importing
        unused Spark.

        Returns
        -------
        Any
            Existing Spark session, or None when unavailable.

        """
        if self.spark is not None:
            return self.spark

        try:
            module = sys.modules.get("pyspark.sql")
            spark_session = getattr(module, "SparkSession", None)

            if spark_session is None:
                return None

            self.spark = spark_session.getActiveSession()
        except Exception:  # noqa: BLE001
            return None

        return self.spark

    def _start_listener(self) -> None:
        """Register the optional JVM listener when an observed action needs it.

        Record unavailable metadata honestly and retain callback cleanup
        ownership.

        """
        if self._listener_attempted:
            return

        spark = self._detect_session()

        if spark is None:
            return

        self._listener_attempted = True

        try:
            gateway = importlib.import_module("pyspark.java_gateway")
            gateway.ensure_callback_server_started(spark.sparkContext._gateway)
            manager = spark._jsparkSession.listenerManager()
            listener = _QueryListener(self)
            manager.register(listener)
            self._listener = listener
            self._listener_manager = manager
        except Exception as error:  # noqa: BLE001
            self._warning(error)

    def start(self) -> None:
        """Arm reversible observation without loading PySpark or its listener.

        Observe existing action methods on the owning thread without forcing
        work.

        """
        global _IMPORT_WATCHER

        if self._active:
            return

        try:
            with _LOCK:
                first = not _ACTIVE
                _ACTIVE.setdefault(self._thread, []).append(self)
                self._active = True

                if first:
                    _IMPORT_WATCHER = _SparkFinder()
                    sys.meta_path.insert(0, _IMPORT_WATCHER)
                    _install_patches()
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        """Drain available query events and restore observer-owned methods.

        Keep workload results intact while detaching the optional JVM
        listener.

        """
        global _IMPORT_WATCHER

        if not self._active:
            return

        if self._listener_manager is not None:
            try:
                self.spark.sparkContext._jsc.sc().listenerBus().waitUntilEmpty(1000)
            except Exception:  # noqa: BLE001
                pass

            try:
                self._listener_manager.unregister(self._listener)
            except Exception as error:  # noqa: BLE001
                self._warning(error)

        self._active = False

        with _LOCK:
            stack = _ACTIVE.get(self._thread, [])

            if self in stack:
                stack.remove(self)

            if not stack:
                _ACTIVE.pop(self._thread, None)

            if not _ACTIVE:
                if _IMPORT_WATCHER is not None and _IMPORT_WATCHER in sys.meta_path:
                    sys.meta_path.remove(_IMPORT_WATCHER)

                _IMPORT_WATCHER = None

                for cls, name, original, wrapper, owned in reversed(_PATCHES):
                    if getattr(cls, name, None) is wrapper:
                        if owned:
                            setattr(cls, name, original)
                        else:
                            delattr(cls, name)

                _PATCHES.clear()

        self._lineage.clear()
        self._owners.clear()

    def _locations(self, obj: Any) -> list[SourceLocation]:
        """Collect relevant project trigger and transformation locations.

        Deduplicate source references without adding a Spark action.

        Parameters
        ----------
        obj : Any
            Object whose behavior is observed or documented.

        Returns
        -------
        list[SourceLocation]
            Deduplicated project source locations for the observed object.

        """
        entry = self._lineage.get(id(obj))

        if entry is None:
            return []

        reference, locations = entry
        return list(locations) if reference() is obj else []

    def _remember(
        self,
        result: Any,
        parent: Any,
        args: Any,
        location: SourceLocation | None,
    ) -> None:
        """Retain lazy DataFrame lineage and writer ownership.

        Keep source references available for a later observed action.

        Parameters
        ----------
        result : Any
            Cell execution result or model being processed.

        parent : Any
            Owning DataFrame or lexical scope used for attribution.

        args : Any
            Positional arguments or syntax parameters forwarded to the
            operation.

        location : [SourceLocation] | None
            Captured project source location, when available.

        """
        if result is None:
            return

        locations = self._locations(parent)

        for argument in args:
            locations.extend(self._locations(argument))

        if location is not None:
            locations.append(location)

        locations = list(dict.fromkeys(locations))

        try:
            reference = weakref.ref(result)
        except TypeError:
            return

        self._lineage[id(result)] = (reference, locations)

        if type(result).__name__.endswith("WriterV2"):
            self._owners[id(result)] = parent

    def _job_ids(self) -> set[int]:
        """Read available job identifiers for an observed Spark action.

        Preserve unavailable status metadata without forcing execution.

        Returns
        -------
        set[int]
            Available Spark job identifiers associated with the action.

        """
        spark = self._detect_session()

        try:
            context = spark.sparkContext
            group = context.getLocalProperty("spark.jobGroup.id")
            return set(context.statusTracker().getJobIdsForGroup(group))
        except Exception:  # noqa: BLE001
            return set()

    def _query_finished(
        self, name: str, query: Any, duration: int | None, status: RunStatus | str
    ) -> None:
        """Match a JVM completion callback to one pending observed action.

        Prefer its actual query plan and reject ambiguous concurrent matches.

        Parameters
        ----------
        name : str
            Identifier, method name, or binding label being inspected.

        query : Any
            JVM query execution supplying plan and completion metadata.

        duration : int | None
            Query duration reported by Spark, in nanoseconds.

        status : [RunStatus] | str
            Observed query completion outcome.

        """
        if not self._active:
            return

        if self._concurrent_queries:
            warning = (
                "Concurrent Spark actions were observed outside the profiling thread;"
                " asynchronous query plans are not attributed."
            )

            if warning not in self.session.result.warnings:
                self.session.result.warnings.append(warning)

            return

        query_id = str(optional_call(query, "id", ""))

        try:
            with self._lock:
                candidates = [(record, key) for record, key in self._pending if key == query_id]

                if not candidates:
                    # Only an unambiguous pending action is eligible. Never guess
                    # when multiple driver actions could own a listener event.
                    candidates = [
                        (record, key)
                        for record, key in self._pending
                        if record.name == name
                        or record.name.startswith("write.")
                        or (
                            record.name in {"collect", "toPandas", "toArrow"}
                            and name == "collectToPython"
                        )
                    ]

                if len(candidates) != 1:
                    return

                execution, key = candidates[0]
                self._pending.remove((execution, key))
                execution.warnings = [
                    warning
                    for warning in execution.warnings
                    if not warning.startswith("Input DataFrame plan only:")
                    and not warning.startswith("AQE final plan")
                ]
                capture_query(execution, query)
                execution.status = RunStatus(status)

                # duration is JVM query wall time; Python action wall duration
                # remains separately measured by record_action below.
                if duration is not None:
                    execution.metadata["query_wall_time_ns"] = duration

                execution.metadata["plan_origin"] = "query execution listener"
        except Exception as error:  # noqa: BLE001
            self._warning(error)

    def record_action(
        self,
        dataframe: Any,
        action: str,
        callback: Callable[[], _T],
        location: SourceLocation | None = None,
    ) -> _T:
        """Observe an action and preserve its return value or exception.

        Parameters
        ----------
        dataframe : [DataFrame]
            Owning [DataFrame]; only its existing plan metadata is inspected.

        action : str
            User-facing action name, such as `"collect"` or
            `"write.parquet"`.

        callback : Callable[[], _T]
            The action to execute exactly once.

        location : [SourceLocation] | None, default=None
            Explicit trigger location. Otherwise the closest user frame is
            used.

        Returns
        -------
        object
            The unchanged return value of `callback`.

        Examples
        --------
        The callback is invoked once and its result is returned unchanged.
        This example simulates an external adapter without requiring Spark.

        ```pycon
        >>> from linescope import Session
        >>> from linescope.spark import SparkIntegration
        >>> integration = SparkIntegration(Session(backend="trace"))
        >>> integration.record_action(object(), "example", lambda: 42)
        42
        ```

        With an existing [DataFrame], an adapter can call
        `integration.record_action(df, "collect", df.collect)` when automatic
        method interception is unavailable.

        """
        self._start_listener()

        if location is None:
            try:
                location = caller_location(self.session)
            except Exception as error:  # noqa: BLE001
                self._warning(error)

        execution = SparkExecution(
            id=uuid4().hex, name=action, location=location, stats=SparkExecutionStats()
        )
        locations = self._locations(dataframe)

        if location is not None:
            locations.append(location)

        query = None

        try:
            query = dataframe._jdf.queryExecution()
        except Exception:  # noqa: BLE001
            pass

        query_id = str(optional_call(query, "id", "")) if query is not None else None

        with self._lock:
            self._pending.append((execution, query_id))

        before = self._job_ids()
        started_at_ms = time_ns() // 1_000_000
        start = perf_counter_ns()
        self._depth += 1

        try:
            return callback()
        except BaseException:
            execution.status = RunStatus.FAILED
            raise
        finally:
            execution.stats.wall_time_ns = perf_counter_ns() - start
            ended_at_ms = time_ns() // 1_000_000
            self._depth -= 1

            try:
                if execution.executed_plan is None and query is not None:
                    capture_query(
                        execution, query, actual=action in {"collect", "toPandas", "toArrow"}
                    )

                if execution.executed_plan is None:
                    execution.warnings.append(
                        "Executed plan unavailable in this Spark environment."
                    )

                execution.jobs = sorted(self._job_ids() - before)
                context = getattr(self.spark, "sparkContext", None)

                if context is not None:
                    execution.stages = stage_statistics(
                        context, execution.jobs, action_window=(started_at_ms, ended_at_ms)
                    )

                    if not self._concurrent_queries:
                        execution.stats.executor_time_ns = _execution_executor_time(
                            context, execution.jobs, execution.stages
                        )

                        if execution.stats.executor_time_ns is not None:
                            execution.metadata["executor_time_scope"] = (
                                "unique completed stage attempts wholly observed during "
                                "this action; "
                                "previously completed/skipped dependencies excluded"
                            )

                if execution.jobs:
                    execution.metadata["job_attribution"] = (
                        "new jobs in the existing job group during this action; concurrent"
                        " jobs may overlap"
                    )
            except Exception as error:  # noqa: BLE001
                self._warning(error)

            try:
                self.session.add_spark_execution(execution, list(dict.fromkeys(locations)))
            except Exception as error:  # noqa: BLE001
                self._warning(error)
