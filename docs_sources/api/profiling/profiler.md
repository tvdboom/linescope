# profiler
----------

Use this alias for [profile], the shared, callable [ProfileController] instance.
Import `profiler` directly from `linescope`. The identity `profiler is profile`
is `True`: calling either name creates a session, and their `start()`, `stop()`,
and `result` operate on the same retained session.

:: linescope:profiler
    :: signature
    :: head
    :: table:
        - parameters
        - attributes:
            include: [result]
        - returns
    :: see also

<br>

## Example

:: examples

<br>

## Methods

:: methods:
    toc_only: False
    include:
        - start
        - stop
        - save
        - show
