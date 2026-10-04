# Memory growth

Use process RAM readings to locate a temporary spike and growth inside a loop.
This example retains 16 MiB of input, briefly creates a 48 MiB processing
buffer, and accumulates four 4 MiB batches before releasing them.

:: example: memory_example.py

Run it from the checkout:

```console
uv run python examples/memory_example.py
```

Open `memory.html` and select Memory. Inspect the chart or move the reading
slider, then follow the source link at a peak. The RAM growth ordering on the
source page shows the accumulated increase from the batch loop.

The separate Python allocation Δ column shows tracked objects still retained
at stop. Buffers created and freed inside the session can leave a visible RAM
peak while their retained allocation change is zero.

RAM is the entire Python process's resident memory. Peak readings can miss very
brief spikes, and freeing a buffer need not immediately return pages to the OS.
See [memory collection](../user_guide/backends.md#memory) for the measurement
scope and overhead.
