# Results

Read `session.result` or `profile.result` to inspect collected measurements,
source snapshots, warnings, and child runs. These objects are returned by
LineScope; normal profiling does not require constructing them directly.

## ProfileResult

:: linescope.model:ProfileResult
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## ProfileRun

:: linescope.model:ProfileRun
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## Example

```pycon
from linescope import profile

with profile(
    backend="trace", display="none", notebooks=False, spark=False
) as session:
    total = sum(range(10))

result = session.result
(total, str(result.backend), str(result.root_run.status))
```
