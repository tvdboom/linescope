# Options and states

Import these enums directly from `linescope`. Use `Backend` and `DisplayMode`
to configure collection; the remaining enums describe returned session and
result data. Enum constructors accept their supported string values.

```pycon
from linescope import Backend, Config, DisplayMode

config = Config(backend=Backend.TRACE, display=DisplayMode.NONE)
(config.backend is Backend.TRACE, config.display is DisplayMode.NONE)
Backend("trace") is Backend.TRACE
```

## Backend

:: linescope.enums:Backend
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## DisplayMode

:: linescope.enums:DisplayMode
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## SessionState

:: linescope.enums:SessionState
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## RunStatus

:: linescope.enums:RunStatus
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## SourceKind

:: linescope.enums:SourceKind
    :: signature
    :: head
    :: table:
        - attributes
    :: see also

## SymbolKind

:: linescope.enums:SymbolKind
    :: signature
    :: head
    :: table:
        - attributes
    :: see also
