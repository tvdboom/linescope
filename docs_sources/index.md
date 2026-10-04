---
template: home.html
hide:
  - navigation
  - toc
---

## Start with the expensive line

LineScope keeps the whole source visible: quiet lines, hot lines, functions,
classes, and notebook cells. The cost of external libraries stays with your
calling line. A report is a single HTML file you can open offline and share
after reviewing its embedded source.

```python
from linescope import profile

with profile(backend="trace", display="none") as session:
    result = sum(value * value for value in range(100_000))

session.save("linescope.html")
```

The default collection engine is **Trace**, which works on every supported
Python version. Enable `memory=True` for Python allocation tracking. Read
[Backends](user_guide/backends.md) before choosing an engine.

[Get started](getting_started.md){ .md-button .md-button--primary }
[Read the API](api/profiling/session.md){ .md-button }
