# register_backend
------------------

:: linescope:register_backend
    :: signature
    :: head
    :: table:
        - parameters
        - returns
    :: see also

Implement [ProfilerBackend] to register a custom collector. Its result uses
[RawBackendResult] and [RawLine] records before LineScope normalizes
measurements for reporting.
