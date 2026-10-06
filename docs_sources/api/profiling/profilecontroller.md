# profile / profiler
--------------------

Use `profile` to create a context-managed session or collect a notebook across
cells with `start()` and `stop()`. `profiler` is an alias for the same
[ProfileController] instance. Import both names directly from `linescope`.

[](){#profilecontroller}
[](){#profile}
[](){#profiler}

:: linescope.api:ProfileController
    :: signature
    :: head
    :: table:
        - parameters
        - attributes:
            include: [result]
        - returns
    :: see also

<br>

## Methods

:: methods:
    toc_only: False
    include:
        - __call__
        - start
        - stop
        - save
        - show
