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

    Attributes
    ----------
    TRACE : [Backend]
        Measure explicit Python trace events in the calling thread.

    SCALENE : [Backend]
        Collect scoped CPU, memory, and optional GPU sampling data.

    TACHYON : [Backend]
        Sample Python 3.15 stacks from a separate process.

    """

    TRACE = "trace"
    SCALENE = "scalene"
    TACHYON = "tachyon"


class DisplayMode(StrEnum):
    """Choose when a profiling session displays its report.

    Display at completion, after each notebook cell, or only on request.

    Attributes
    ----------
    END : [DisplayMode]
        Display one report when profiling completes.

    CELL : [DisplayMode]
        Display the current report after each notebook cell.

    NONE : [DisplayMode]
        Display reports only when explicitly requested.

    """

    END = "end"
    CELL = "cell"
    NONE = "none"


class SessionState(StrEnum):
    """Track the lifecycle of a single profiling session.

    A session starts once and remains stopped after cleanup.

    Attributes
    ----------
    CREATED : [SessionState]
        Represent a session whose collection has not started.

    RUNNING : [SessionState]
        Represent a session with active instrumentation.

    STOPPED : [SessionState]
        Represent a finalized session after cleanup.

    """

    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"


class SourceKind(StrEnum):
    """Distinguish project files from captured notebook source.

    Both kinds preserve the full source snapshot for reporting.

    Attributes
    ----------
    PYTHON : [SourceKind]
        Identify a snapshotted project Python file.

    NOTEBOOK : [SourceKind]
        Identify captured notebook or cell source.

    """

    PYTHON = "python"
    NOTEBOOK = "notebook"


class SymbolKind(StrEnum):
    """Classify a statically resolved project definition.

    Definitions identify functions, classes, methods, or notebooks.

    Attributes
    ----------
    FUNCTION : [SymbolKind]
        Identify a function definition outside a class body.

    CLASS : [SymbolKind]
        Identify a project class definition.

    METHOD : [SymbolKind]
        Identify a function defined in a class body.

    NOTEBOOK : [SymbolKind]
        Identify a captured notebook navigation target.

    """

    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    NOTEBOOK = "notebook"


class RunStatus(StrEnum):
    """Describe the outcome or reference role of a profiled run.

    Inline notebook references belong to their parent's execution.

    Attributes
    ----------
    SUCCESS : [RunStatus]
        Mark a workload that completed successfully.

    FAILED : [RunStatus]
        Mark a workload that raised an execution error.

    REFERENCE : [RunStatus]
        Mark an inline notebook reference owned by its parent.

    """

    SUCCESS = "success"
    FAILED = "failed"
    REFERENCE = "reference"


class NotebookCollection(StrEnum):
    """Describe the data captured for a separate notebook invocation.

    Distinguish source snapshots and collected profiles from parent waits.

    Attributes
    ----------
    WAIT_ONLY : [NotebookCollection]
        Record only the parent waiting for a child invocation.

    SOURCE_ONLY : [NotebookCollection]
        Include child source without child measurements.

    MERGED : [NotebookCollection]
        Include separately collected child measurements.

    """

    WAIT_ONLY = "parent wait only"
    SOURCE_ONLY = "child source only"
    MERGED = "child profile merged"
