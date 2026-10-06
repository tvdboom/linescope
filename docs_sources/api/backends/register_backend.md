# register_backend
------------------

:: linescope:register_backend
    :: signature
    :: head
    :: table:
        - parameters
        - returns
    :: see also

## Collector contract

Use this interface when registering your own backend. Built-in collectors are
selected through `profile(backend=...)`; their implementation classes are not
needed for normal profiling.

## ProfilerBackend

:: linescope.backends:ProfilerBackend
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

:: methods:
    include: [start, stop, result]

## RawBackendResult

:: linescope.backends:RawBackendResult
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## RawLine

:: linescope.backends:RawLine
    :: signature
    :: head
    :: table:
        - attributes
    :: see also
