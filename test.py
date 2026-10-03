"""Manual smoke example, in the same role as Backtide's top-level test.py.

Run ``uv run python test.py`` to generate a local report. The automated test
suite lives in ``tests`` and does not rely on this interactive scratch entry point.
"""

from examples.profile_script import main

if __name__ == "__main__":
    main()
