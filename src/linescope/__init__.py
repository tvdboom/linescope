"""Profile your own Python source, notebooks, and Spark driver actions."""

from linescope.api import ProfileController, Session, profile, profiler
from linescope.backends import register_backend
from linescope.config import Config, configure

__version__ = "0.1.0"


def load_ipython_extension(shell):
    """Register the optional IPython cell magics when loading this extension."""
    from linescope.notebooks.ipython import load_ipython_extension as load

    load(shell)


def unload_ipython_extension(shell):
    """Restore IPython magics when unloading this extension."""
    from linescope.notebooks.ipython import unload_ipython_extension as unload

    unload(shell)


__all__ = [
    "Config",
    "ProfileController",
    "Session",
    "__version__",
    "configure",
    "load_ipython_extension",
    "profile",
    "profiler",
    "register_backend",
    "unload_ipython_extension",
]
