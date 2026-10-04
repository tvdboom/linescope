"""LineScope.

Author: Mavs
Description: Explicit correlation and merging for separately profiled notebook
runs.

"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from uuid import uuid4

from linescope.enums import NotebookCollection, RunStatus
from linescope.model import ProfileResult


@dataclass(frozen=True)
class ChildContext:
    """Carry portable correlation information for a child notebook.

    Transport these values through your own approved notebook parameters
    or artifact store. LineScope does not silently change notebook arguments.

    Parameters
    ----------
    correlation_id : str
        Unique identifier for this invocation.

    parent_id : str
        Parent profile run identifier.

    Attributes
    ----------
    correlation_id : str
        Unique token linking a child profile to its parent invocation.

    parent_id : str
        Identifier of the parent run that owns the child execution.

    See Also
    --------
    - linescope.notebooks:merge_child
    - linescope.model:ProfileRun
    - linescope.notebooks:NotebookIntegration

    Examples
    --------
    ```pycon
    >>> from linescope.notebooks import ChildContext
    >>> context = ChildContext.create("parent-run")
    >>> context.as_parameters()["linescope_parent_id"]
    'parent-run'
    ```

    """

    correlation_id: str
    parent_id: str

    @classmethod
    def create(cls, parent_id: str) -> ChildContext:
        """Create an independent correlation token for a child invocation.

        Parameters
        ----------
        parent_id : str
            Identifier of the profile run that invokes the child notebook.

        Returns
        -------
        [ChildContext]
            Fresh invocation ID paired with its parent run.

        """
        return cls(uuid4().hex, parent_id)

    def as_parameters(self) -> dict[str, str]:
        """Return explicit string parameters suitable for a notebook call.

        Returns
        -------
        dict[str, str]
            Correlation and parent identifiers. Passing these parameters is
            an explicit choice; no notebook arguments are changed implicitly.

        """
        return {
            "linescope_correlation_id": self.correlation_id,
            "linescope_parent_id": self.parent_id,
        }


def merge_child(parent: ProfileResult, child: ProfileResult, correlation_id: str) -> ProfileResult:
    """Merge a child profile into a recorded notebook invocation.

    Raise `ValueError` if the invocation is unknown, ownership does not
    match, or source IDs collide with different source content.

    Parameters
    ----------
    parent : [ProfileResult]
        Parent result containing a child invocation with the correlation ID.

    child : [ProfileResult]
        Independently collected child profile with matching parent metadata.

    correlation_id : str
        Invocation ID assigned by the parent.

    Returns
    -------
    [ProfileResult]
        Independent merged result. The input results remain unchanged.

    See Also
    --------
    - linescope.notebooks:ChildContext
    - linescope.model:ProfileResult
    - linescope.model:ProfileRun

    Examples
    --------
    ```pycon
    >>> from linescope import Session
    >>> from linescope.model import ProfileRun
    >>> from linescope.notebooks import merge_child
    >>> parent = Session(backend="trace").result
    >>> invocation = ProfileRun(id="child-call", elapsed_ns=12)
    >>> parent.root_run.children.append(invocation)
    >>> child = Session(backend="trace").result
    >>> merged = merge_child(parent, child, "child-call")
    >>> merged.root_run.children[0].metadata["parent_wait_time_ns"]
    12
    ```

    """
    merged = deepcopy(parent)
    nodes = [merged.root_run]

    while nodes:
        owner = nodes.pop()

        for index, invocation in enumerate(owner.children):
            if invocation.id != correlation_id:
                nodes.append(invocation)
                continue

            if child.root_run.parent_id not in (None, owner.id):
                raise ValueError("Child profile belongs to a different parent run.")

            declared = child.root_run.metadata.get("correlation_id")

            if declared is not None and declared != correlation_id:
                raise ValueError("Child profile correlation ID does not match.")

            for source_id, source in child.sources.items():
                existing = merged.sources.get(source_id)

                if existing is not None and existing != source:
                    raise ValueError(f"Source snapshot collision: {source_id}")

            # Keep the parent's invocation identity and wait duration while
            # attaching the independently collected child measurements.
            replacement = deepcopy(child.root_run)
            replacement.id = invocation.id
            replacement.parent_id = owner.id
            if invocation.status == RunStatus.FAILED:
                replacement.status = invocation.status

            replacement.metadata = {**invocation.metadata, **replacement.metadata}
            replacement.metadata["correlation_id"] = correlation_id
            replacement.metadata["parent_wait_time_ns"] = invocation.elapsed_ns
            replacement.metadata["collection"] = (
                NotebookCollection.MERGED
                if child.capabilities.line_time
                else NotebookCollection.SOURCE_ONLY
            )
            replacement.metadata["child_backend"] = child.backend
            replacement.metadata["child_capabilities"] = asdict(child.capabilities)
            replacement.metadata["child_source_ids"] = list(child.sources)

            for nested in replacement.children:
                if nested.parent_id == child.root_run.id:
                    nested.parent_id = replacement.id

            owner.children[index] = replacement
            merged.sources.update(deepcopy(child.sources))

            for symbol in child.symbols:
                if symbol not in merged.symbols:
                    merged.symbols.append(deepcopy(symbol))

            for warning in child.warnings:
                message = f"Child {correlation_id}: {warning}"

                if message not in merged.warnings:
                    merged.warnings.append(message)

            return merged

    raise ValueError(f"Unknown child invocation: {correlation_id}")
