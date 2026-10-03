# Spark integrations

The Spark integration is optional and imports runtime-specific objects only when needed.
`spark=True` requests observation; the default `"auto"` mode detects an available session.

## Normalized records

- [SparkExecution](model.md#SparkExecution) records the action, source location, plans, status,
  jobs/stages where available, and warnings.
- [SparkOperator](model.md#SparkOperator) represents the physical plan tree and its metrics.
- [SparkExecutionStats](model.md#SparkExecutionStats) keeps wall time separate from cumulative
  executor work and Python memory.

## Adapter boundaries

`linescope.spark.plans` normalizes accessible JVM plans and textual fallbacks. Metrics are
normalized separately; unsupported counters stay absent. The observer restores patched action
methods after collection and never adds an action to a transformation chain.

These modules are integration extension points. Spark/JVM internals differ between releases,
so callers should prefer the normalized model over relying on a particular physical node name.

## SparkIntegration

:: linescope.spark:SparkIntegration
    :: signature
    :: head
    :: table:
        - parameters
        - returns
    :: methods

## capture_query

:: linescope.spark:capture_query
    :: signature
    :: head
    :: table:
        - parameters
        - returns

## parse_plan

:: linescope.spark:parse_plan
    :: signature
    :: head
    :: table:
        - parameters
        - returns
