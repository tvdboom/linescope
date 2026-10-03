# API reference

The public API provides scoped and explicit profiling, configuration, normalized result models,
and extension points. These pages use the same NumPy-docstring-driven rendering conventions as
Backtide. Source links point into the repository.

| Interface | Purpose |
| --- | --- |
| [Profiling](profiling.md) | Context managers, sessions, start/stop, save/show |
| [Configuration](configuration.md) | Process/project defaults and session settings |
| [Data model](model.md) | Source, line, function, run, and Spark records |
| [Source and symbols](source.md) | Project discovery, snapshots, and symbol navigation |
| [Backends](backends.md) | Collection protocol and backend registration |
| [Notebooks](notebooks.md) | Source capture, extension, and child correlation |
| [Spark](spark.md) | Observed actions, plans, metrics, and attribution |

All source lines use one-based line numbers. Token columns use zero-based character offsets.
Times in the normalized model use nanoseconds; bytes remain bytes until formatted for display.
Optional values remain `None` when no supported measurement exists.
