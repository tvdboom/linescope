# ProfileResult

Read `session.result` or `profile.result` for the complete profile, including
source snapshots, warnings, and child runs. Normal profiling returns this
object without requiring you to construct it.

:: linescope.model:ProfileResult
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
