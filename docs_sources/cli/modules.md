# Profile a module

```console
linescope --backend trace --include mypackage -m mypackage.job
```

Module execution follows Python's `-m` behavior, including package-relative imports. Run from the
project root, or install your package into the environment before invoking LineScope. Options
after the module name are passed to the module.

The [package example](../examples/package.md) contains a small runnable package and a helper
function in a second module so you can inspect navigation across files.
