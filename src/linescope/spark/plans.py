"""LineScope.

Author: Mavs
Description: Normalize Spark JVM query plans without importing PySpark.

"""

from __future__ import annotations

from typing import Any

from linescope.model import SparkExecution, SparkOperator
from linescope.spark.metrics import operator_metrics, optional_call, scala_items


def parse_plan(plan: Any, *, max_nodes: int = 1000) -> list[SparkOperator]:
    """Snapshot a physical plan, preferring AQE's current executed plan.

    This function only reads plan objects. It never calls `execute`,
    `collect`, `count`, or any other Spark action.

    Parameters
    ----------
    plan : object
        JVM physical plan. Adaptive and query-stage wrappers are unwrapped.

    max_nodes : int, default=1000
        Bound on traversed nodes for unusually large or cyclic plans.

    Returns
    -------
    list[[SparkOperator]]
        Plan roots with nested children and raw Spark metrics.

    See Also
    --------
    - linescope.spark:capture_query
    - linescope.spark:SparkIntegration
    - linescope.model:SparkOperator

    Examples
    --------
    ```pycon
    >>> from linescope.spark import parse_plan

    >>> parse_plan(None)
    []
    ```

    """
    seen: set[str] = set()

    def visit(node: Any) -> SparkOperator | None:
        """Normalize a physical plan node while bounding recursive traversal.

        Avoid cycles and unwrap supported AQE or query-stage nodes without
        forcing execution.

        Parameters
        ----------
        node : Any
            Syntax or plan node being visited or resolved.

        Returns
        -------
        [SparkOperator] | None
            Normalized result of this operation.

        """
        if node is None or len(seen) >= max_nodes:
            return None

        name = str(optional_call(node, "nodeName", type(node).__name__))

        # AQE wrappers can outlive the plan they started with. Read the current
        # executed plan without requesting another distributed action.
        if "AdaptiveSparkPlan" in name:
            current = optional_call(node, "executedPlan")

            if current is not None:
                node = current
                name = str(optional_call(node, "nodeName", name))

        node_id = str(optional_call(node, "id", id(node)))

        if node_id in seen:
            return None

        seen.add(node_id)
        description = optional_call(node, "simpleString", None)

        if description is None:
            try:
                description = node.simpleString(200)
            except Exception:  # noqa: BLE001
                description = name

        operator = SparkOperator(
            id=node_id,
            name=name,
            description=str(description),
            metrics=operator_metrics(node),
        )
        children = scala_items(optional_call(node, "children"))

        if "QueryStage" in name:
            stage_plan = optional_call(node, "plan")

            if stage_plan is not None:
                children.append(stage_plan)

        for child in children:
            normalized = visit(child)

            if normalized is not None:
                operator.children.append(normalized)

        return operator

    root = visit(plan)
    return [root] if root is not None else []


def capture_query(execution: SparkExecution, query: Any, *, actual: bool = True):
    """Populate an execution from an observed JVM query execution.

    Parameters
    ----------
    execution : [SparkExecution]
        Normalized action record to update.

    query : object
        JVM `QueryExecution` instance.

    actual : bool, default=True
        Whether this is the action's query execution. False explicitly labels
        an input-[DataFrame] fallback when a listener is unavailable.

    See Also
    --------
    - linescope.spark:parse_plan
    - linescope.model:SparkExecution
    - linescope.spark:SparkIntegration

    Examples
    --------
    Missing JVM access leaves unsupported measurements absent.

    ```pycon
    >>> from linescope.model import SparkExecution
    >>> from linescope.spark import capture_query

    >>> execution = SparkExecution("example")
    >>> capture_query(execution, None)
    >>> execution.executed_plan is None
    True
    ```

    """
    for field, method in (
        ("parsed_plan", "logical"),
        ("analyzed_plan", "analyzed"),
        ("optimized_plan", "optimizedPlan"),
        ("initial_plan", "sparkPlan"),
    ):
        plan = optional_call(query, method)
        value = optional_call(plan, "toString")

        if value is not None:
            setattr(execution, field, str(value))

            if field == "initial_plan":
                execution.metadata["initial_plan_origin"] = (
                    "physical plan before execution preparation"
                )

    plan = optional_call(query, "executedPlan")

    if plan is not None:
        execution.executed_plan = str(optional_call(plan, "toString", ""))
        execution.operators = parse_plan(plan)

        if "AdaptiveSparkPlan" in str(optional_call(plan, "nodeName", "")):
            initial = optional_call(plan, "initialPlan")
            initial_text = optional_call(initial, "toString")

            if initial_text is not None:
                execution.initial_plan = str(initial_text)
                execution.metadata["initial_plan_origin"] = "AQE initial physical plan"

            final = optional_call(plan, "isFinalPlan")

            if isinstance(final, bool):
                execution.metadata["aqe_final_plan"] = final

            if final is not True:
                execution.warnings.append(
                    "AQE final plan is not available; showing its current plan."
                )

    execution.metadata["plan_origin"] = (
        "action query execution" if actual else "input DataFrame; action query unavailable"
    )

    if not actual:
        execution.warnings.append(
            "Input DataFrame plan only: the action's query listener was unavailable. "
            "Count and write actions may execute a different physical plan."
        )
