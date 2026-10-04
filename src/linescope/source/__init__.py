"""LineScope.

Author: Mavs
Description: Project source discovery and reliable symbol navigation.

"""

from linescope.source.discovery import SourceRegistry, discover_root
from linescope.source.symbols import SymbolIndex, build_navigation

__all__ = ["SourceRegistry", "SymbolIndex", "build_navigation", "discover_root"]
