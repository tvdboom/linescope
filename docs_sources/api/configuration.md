# Configuration

## Config

:: linescope.config:Config
    :: signature
    :: head
    :: table:
        - parameters
    :: examples

## configure

:: linescope:configure
    :: signature
    :: head
    :: table:
        - parameters
        - returns
    :: examples

## Project settings

The `[tool.linescope]` table in `pyproject.toml` uses the same option names as `profile`.
See [configuration](../user_guide/configuration.md) for defaults and precedence.

```pycon
from linescope.model import BackendCapabilities
BackendCapabilities().hit_counts
```
