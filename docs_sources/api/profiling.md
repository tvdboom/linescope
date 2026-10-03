# Profiling

```python
from linescope import configure, profile, profiler
```

`profile` is callable and also exposes `start`, `stop`, `save`, and `show`. `profiler` refers to
the same controller. A context manager returns a session whose completed `result` contains the
normalized measurements and source snapshots.

```python
with profile(backend="trace", display="none") as session:
    total = sum(range(1000))

session.save("linescope.html")
```

## Session

:: linescope.api:Session
    :: signature
    :: head
    :: table:
        - parameters
        - attributes
    :: examples
    :: methods

## Explicit lifecycle

Start once, run your workload, then stop in a `finally` block when managing a scope manually.
Context managers are usually easier because they also restore hooks after exceptions. Saving a
report does not resume collection. `display="none"` supports batch and server environments.

## profile / profiler

Both names refer to one `ProfileController`. Calling it constructs a session; `start` also
starts it immediately. `stop` returns the completed `ProfileResult`.

:: linescope.api:ProfileController
    :: signature
    :: head
    :: methods:
        include:
            - __call__
            - start
            - stop
            - save
            - show
