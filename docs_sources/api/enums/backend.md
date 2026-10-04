# Backend
---------

:: linescope.enums:Backend
    :: signature
    :: head
    :: see also

Members: `Backend.TRACE`, `Backend.SCALENE`, and `Backend.TACHYON`.

Use the standard enum constructor to convert a supported string. Configuration
and `create_backend` accept either enum members or strings; registered custom
backend names remain strings.

```pycon
from linescope import Backend, Config, DisplayMode

Backend("trace") is Backend.TRACE
config = Config(backend=Backend.TRACE, display=DisplayMode.NONE)
config.backend is Backend.TRACE
Config(backend="trace").backend is Backend.TRACE
```

```python
from linescope import Backend, DisplayMode, profile

with profile(backend=Backend.TRACE, display=DisplayMode.NONE) as session:
    total = sum(range(1000))
```
