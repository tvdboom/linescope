# profile
---------

Use this shared, callable [ProfileController] instance to create sessions or
collect across notebook cells. Import `profile` directly from `linescope`.
[profiler] is another name for the same object: `profiler is profile` is `True`.
Both names share the session started with `start()` and its result.

:: linescope:profile
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
