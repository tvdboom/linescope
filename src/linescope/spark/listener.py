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
import threading
from time import perf_counter_ns, time_ns
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

    """

    ACTION = "action"
    WRITE = "write"
    TRANSFORM = "transform"


def _current() -> SparkIntegration | None:
    with _LOCK:
        stack = _ACTIVE.get(threading.get_ident(), [])
        return stack[-1] if stack else None


def _install_class(cls: type, names: set[str], kind: _MethodKind) -> None:
    for name in sorted(names):
        original = getattr(cls, name, None)

        if not callable(original) or any(c is cls and n == name for c, n, *_ in _PATCHES):
            continue

        owned = name in vars(cls)

        def wrap(method: Any, method_name: str) -> Any:
            @functools.wraps(method)
            def observed(obj: Any, *args: Any, **kwargs: Any) -> Any:
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


def _install_patches() -> None:
    specifications = [
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

    for module_name, class_name, names, kind in specifications:
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name)
        except (ImportError, AttributeError):
            continue

        _install_class(cls, names, kind)


class _QueryListener:
    class Java:
        implements: ClassVar[list[str]] = ["org.apache.spark.sql.util.QueryExecutionListener"]

    def __init__(self, integration: SparkIntegration) -> None:
        self.integration = integration

    def onSuccess(self, name: str, query: Any, duration: int) -> None:
        self.integration._query_finished(str(name), query, int(duration), RunStatus.SUCCESS)

    def onFailure(self, name: str, query: Any, error: Any) -> None:
        del error
        self.integration._query_finished(str(name), query, None, RunStatus.FAILED)


class SparkIntegration:
    """Observe driver [DataFrame] actions without introducing Spark actions.

    Instrumentation applies to the Python thread that starts the adapter.
    Other threads and unrelated sessions continue through their original
    methods. A JVM query listener captures actual action plans when callback
    access is allowed. Restricted environments receive a clearly labeled
    input-plan fallback. Executor Python UDFs are outside this scope.

    Parameters
    ----------
    session : [Session]
        Owning LineScope session.

    spark : [SparkSession] | None, default=None
        Existing Spark session. No Spark cluster/session is created by this
        adapter; an active session is detected when available.

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
        self.session = session
        self.spark = spark
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
        message = (
            f"Spark metadata unavailable ({type(error).__name__}); user execution was preserved."
        )

        if message not in self.session.result.warnings:
            self.session.result.warnings.append(message)

    def _detect_session(self) -> Any:
        if self.spark is not None:
            return self.spark

        try:
            spark_session = importlib.import_module("pyspark.sql").SparkSession
            self.spark = spark_session.getActiveSession()
        except Exception:  # noqa: BLE001
            return None

        return self.spark

    def _start_listener(self) -> None:
        spark = self._detect_session()

        if spark is None or self._listener_attempted:
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
        """Install reversible action/lineage observers and an optional listener.

        Observe existing action methods on the owning thread without forcing
        work.

        """
        if self._active:
            return

        with _LOCK:
            if not _ACTIVE:
                _install_patches()

            _ACTIVE.setdefault(self._thread, []).append(self)
            self._active = True

        self._start_listener()

    def stop(self) -> None:
        """Drain available query events and restore observer-owned methods.

        Keep workload results intact while detaching the optional JVM
        listener.

        """
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
