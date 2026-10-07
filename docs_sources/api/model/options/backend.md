# Backend

Import `Backend` from `linescope` to select a built-in collector. Registered
custom collectors continue to use their own string names.

:: linescope.enums:Backend
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## Example

```pycon
from linescope import Backend, Config, DisplayMode

config = Config(backend=Backend.TRACE, display=DisplayMode.NONE)
(config.backend is Backend.TRACE, config.display is DisplayMode.NONE)
Backend("trace") is Backend.TRACE
```
