# Memory growth

Use process RAM readings to locate a temporary spike and growth inside a loop.
This example retains about 16.8 MB of input, briefly creates a 50.3 MB
processing buffer, and accumulates four 4.2 MB batches before releasing them.

:: example: memory_example.py

Run it from the checkout:

```console
uv run python examples/memory_example.py
```

Open `memory.html` and select Memory. Inspect the chart or move the reading
slider, then follow the source link at a peak. The Mem Growth ordering on the
source page shows the accumulated increase from the batch loop. Select Mem
Growth under Heatmap by to highlight growing lines without changing their order.

Retained Python allocation changes remain available in the collected profile
data. Buffers created and freed inside the session can leave a visible process
memory peak while their retained allocation change is zero.

RAM is the entire Python process's resident memory. Peak readings can miss very
brief spikes, and freeing a buffer need not immediately return pages to the OS.
See [memory collection](../user_guide/backends.md#memory) for the measurement
scope and overhead.
