"""LineScope.

Author: Mavs
Description: Define the fixed vocabularies used by configuration and profile
models.  Use the enum constructor to convert a string to its corresponding
member. Members retain their string values when displayed or serialized as JSON.

"""

from enum import StrEnum


class Backend(StrEnum):
    """Select a built-in measurement backend.

    Registered custom backends continue to use their own string names.

    """

    TRACE = "trace"
    SCALENE = "scalene"
    TACHYON = "tachyon"


class DisplayMode(StrEnum):
    """Choose when a profiling session displays its report.

    Display at completion, after each notebook cell, or only on request.

    """

    END = "end"
    CELL = "cell"
    NONE = "none"


class SparkMode(StrEnum):
    """Select automatic observation of an already loaded Spark environment.

    Explicit enablement and disablement continue to use booleans.

    """

    AUTO = "auto"


class SessionState(StrEnum):
    """Track the lifecycle of a single profiling session.

    A session starts once and remains stopped after cleanup.

    """

    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"


class SourceKind(StrEnum):
    """Distinguish project files from captured notebook source.

    Both kinds preserve the full source snapshot for reporting.

    """

    PYTHON = "python"
    NOTEBOOK = "notebook"


class SymbolKind(StrEnum):
    """Classify a statically resolved project definition.

    Definitions identify functions, classes, methods, or notebooks.

    """

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    NOTEBOOK = "notebook"


class RunStatus(StrEnum):
    """Describe the outcome or reference role of a profiled run.

    Inline notebook references belong to their parent's execution.

    """

    SUCCESS = "success"
    FAILED = "failed"
    REFERENCE = "reference"
