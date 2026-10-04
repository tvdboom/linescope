"""LineScope.

Author: Mavs
Description: Module containing the documentation rendering.

"""

from __future__ import annotations

from dataclasses import MISSING, fields, is_dataclass
from enum import Enum
from html import escape
import importlib
from inspect import (
    Parameter,
    getdoc,
    getmembers,
    getsourcelines,
    isbuiltin,
    isclass,
    isfunction,
    ismethod,
    isroutine,
    signature,
)
import json
from types import MethodType

from click import Command
from mkdocs.config.defaults import MkDocsConfig
import regex as re
import yaml

# Variables ======================================================== >>

LINESCOPE_URL = "https://github.com/tvdboom/linescope/blob/main/src/"

# Mapping of keywords to urls
# Usage in docs: [anchor][key] or [key][] -> [anchor][value]
CUSTOM_URLS = {
    "databricks-16-4-lts": ("https://docs.databricks.com/aws/en/release-notes/runtime/16.4lts"),
    "code-of-conduct": (
        "https://github.com/tvdboom/linescope/blob/main/.github/CODE_OF_CONDUCT.md"
    ),
    "java": "https://spark.apache.org/docs/latest/api/python/getting_started/install.html",
    "dataframe": (
        "https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/"
        "api/pyspark.sql.DataFrame.html"
    ),
    "sparksession": (
        "https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/"
        "api/pyspark.sql.SparkSession.html"
    ),
    "sparkcontext": (
        "https://spark.apache.org/docs/latest/api/python/reference/api/pyspark.SparkContext.html"
    ),
    "interactiveshell": (
        "https://ipython.readthedocs.io/en/stable/api/generated/"
        "IPython.core.interactiveshell.html#IPython.core.interactiveshell.InteractiveShell"
    ),
}

# These names resolve to API pages through MkDocs autorefs. Keep container
# punctuation outside the reference, for example list[[SourceUnit]].
LINKED_TYPES = {
    "Backend",
    "BackendCapabilities",
    "ChildContext",
    "Config",
    "DatabricksIntegration",
    "DataFrame",
    "DisplayMode",
    "FunctionStats",
    "GPUStats",
    "InteractiveShell",
    "LineStats",
    "MemoryStats",
    "NotebookIntegration",
    "ProfileController",
    "ProfilerBackend",
    "ProfileResult",
    "ProfileRun",
    "RawBackendResult",
    "RawLine",
    "RunStatus",
    "ScaleneBackend",
    "Session",
    "SessionState",
    "SourceKind",
    "SourceLocation",
    "SourceRegistry",
    "SourceUnit",
    "SparkContext",
    "SparkExecution",
    "SparkExecutionStats",
    "SparkIntegration",
    "SparkOperator",
    "SparkMode",
    "SparkSession",
    "SymbolDefinition",
    "SymbolIndex",
    "SymbolKind",
    "SymbolRef",
    "TachyonBackend",
    "TraceBackend",
}

FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
LINK_RE = re.compile(r"(?<!\w)\[([.`': \w_-]+?)](?!\()(?:\s*\[(?!\[)([\w_:-]+?)])?")

# Classes ========================================================== >>

# Check if an object is an enum
check_is_enum = lambda obj: isinstance(obj, Enum) or (isclass(obj) and issubclass(obj, Enum))

# Check if an object is a dataclass
check_is_dataclass = lambda obj: is_dataclass(obj)


class AutoDocs:
    """Parses an object to documentation in markdown/html.

    The docstring should follow the numpydoc style[^1]. Blocks should
    start with `::`. The following blocks are accepted:

    - toc
    - tags
    - head (summary + description)
    - summary (first line of docstring, required)
    - description (detailed explanation, can contain admonitions)
    - parameters
    - attributes
    - returns
    - yields
    - see also
    - references
    - examples
    - hyperparameters
    - methods

    Parameters
    ----------
    obj : object
        Class, method or function to parse.

    method : str | None, default=None
        Method of `obj` to parse.

    References
    ----------
    [1] https://numpydoc.readthedocs.io/en/latest/format.html

    """

    # Blocks that can be encountered in the object's docstring
    blocks = (
        "Parameters\n---------",
        "Attributes\n---------",
        "Returns\n-------",
        "Yields\n------",
        "See Also\n--------",
        "References\n-------",
        "Examples\n--------",
        r"\Z",
    )

    def __init__(self, obj: type[object], method: str | None = None):
        if method:
            self.obj = getattr(obj, method)
            self._parent_cls = obj
            self._parent_anchor = f"{obj.__name__.lower()}-"
        else:
            self.obj = obj
            self._parent_cls = None
            self._parent_anchor = ""

        self.method = method
        self.module = obj.__module__

        if isinstance(self.obj, Command):  # Cli commands have no __name__
            self.name = str(self.obj.name)
        else:
            self.name = self.obj.__name__

        if doc := getdoc(self.obj):
            self.doc = doc
        else:
            raise ValueError(f"Object {self.obj} has no docstring.")

    @staticmethod
    def get_obj(command: str) -> AutoDocs:
        """Get an [AutoDocs] object from a string.

        The provided string must be of the form module:object or
        module:object.method.

        Parameters
        ----------
        command : str
            Line with the module and object.

        Returns
        -------
        [AutoDocs]
            New instance of the class.

        """
        if command.startswith("- "):
            command = command.removeprefix("- ")

        module, name = command.split(":")

        if "." in name:
            name, method = name.split(".")
            cls = getattr(importlib.import_module(module), name)
            return AutoDocs(cls, method=method)
        else:
            obj = getattr(importlib.import_module(module), name)
            # Public controllers expose a callable instance of the class
            # documented on the API page, such as profile / profiler.
            return AutoDocs(obj if hasattr(obj, "__name__") else type(obj))

    @staticmethod
    def parse_body(body: str) -> str:
        """Parse a parameter's body to the right Markdown format.

        Allow lists to not have to start with a new line when there's no
        preceding line.

        Parameters
        ----------
        body : str
            A parameter's body.

        Returns
        -------
        str
            The body parsed to accept the docstring list format.

        """
        text = "\n"

        if body.lstrip().startswith(("- ", "* ", "+ ")):
            text += "\n"

        text += "".join([b if b == "\n" else b[4:] for b in body.splitlines(keepends=True)])

        return text + "\n"

    def get_toc(self) -> str:
        """Return a toc of the objects in self.

        Note that object must be iterable.

        Returns
        -------
        str
            Toc of the objects.

        """
        toc = "<table markdown style='font-size: 0.9em'>"

        for obj in self.obj:  # ty: ignore[not-iterable]
            func = AutoDocs(obj)

            name = f"[{obj.__name__}][] ({obj.acronym})"
            toc += f"<tr><td>{name}</td><td>{func.get_summary()}</td></tr>"

        toc += "</table>"
        return toc

    def get_signature(self) -> str:
        """Return the object's signature.

        Returns
        -------
        str
            Object's signature.

        """
        params = signature(self.obj).parameters
        documented_defaults = self._documented_defaults()

        # Assign an object type
        if check_is_dataclass(self.obj):
            obj = "dataclass"
        elif isclass(self.obj):
            if check_is_enum(self.obj):
                obj = "enum"
            else:
                obj = "class"
        elif "cls" in params or isinstance(self.obj, MethodType):
            obj = "classmethod"
        elif "self" in params or self._parent_cls:
            obj = "method"
        elif isinstance(self.obj, Command):
            obj = "command"
        else:
            obj = "function"

        if obj not in ("enum", "dataclass", "command"):
            # Get signature without self, cls and type hints
            sign = []

            for k, v in params.items():
                if k not in ("cls", "self") and not k.startswith("_"):
                    if v.default == Parameter.empty:
                        if "**" in str(v):
                            sign.append(f"**{k}")  # Add ** to kwargs
                        elif "*" in str(v):
                            sign.append(f"*{k}")  # Add * to args
                        else:
                            sign.append(k)
                    else:
                        if v.default is Ellipsis and k in documented_defaults:
                            sign.append(f"{k}={documented_defaults[k]}")
                        elif isinstance(v.default, str):
                            sign.append(f'{k}="{v.default}"')
                        else:
                            sign.append(f"{k}={v.default}")

            parameters = f"({', '.join(sign)})"
        else:
            parameters = ""

        if "linescope" in self.module:
            # Module and filename sep by /
            url = f"{LINESCOPE_URL}{self.module.replace('.', '/')}.py"
        else:
            url = ""

        anchor = f"[](){{#{self._parent_anchor}{self.name.strip('_')}}}\n"

        if obj in ("command", "method"):
            module = ""
        else:
            module = self.module + "."

        obj = f"<em>{obj}</em>"
        name = f"<strong style='color:#0f766e'>{self.name}</strong>"

        if url:
            try:
                line = getsourcelines(self.obj)[1]
                url = f"<span style='float:right'><a href={url}#L{line}>[source]</a></span>"
            except (OSError, TypeError):  # Unavailable source
                url = ""

        # \n\n in front of signature to break potential lists in markdown
        return f"\n\n{anchor}<div class='sign'>{obj} {module}{name}{parameters}{url}</div>\n\n"

    def _documented_defaults(self) -> dict[str, str]:
        """Return documented default values for abbreviated signatures.

        Use parameter defaults from the docstring when a signature uses
        Ellipsis.

        """
        block = self.get_block("Parameters")

        if not block:
            return {}

        defaults = {}

        for header in re.findall(r"^[a-zA-Z*]\w*\s*:.*$", block, re.M):
            match = re.search(r"(?<=default=).+?$", header)

            if match:
                defaults[header.split(":", 1)[0].strip("* ")] = match.group().strip()

        return defaults

    def get_summary(self) -> str:
        """Return the object's summary.

        The summary is the first line of the docstring.

        Returns
        -------
        str
            Object's summary.

        """
        return next(filter(None, self.doc.splitlines()))  # Get first non-empty line

    def get_description(self) -> str:
        """Return the object's description.

        The description is the first part of the docstring where the
        object is explained (before any other block). The summary is
        excluded.

        Returns
        -------
        str
            Object's description.

        """
        pattern = f".*?(?={'|'.join(self.blocks)})"
        match = re.match(pattern, self.doc[len(self.get_summary()) :], re.S)
        description = match.group() if match else ""

        if isclass(self.obj) and check_is_enum(self.obj):
            if getattr(self.obj, "__members__", None):
                members = self.obj.__members__  # ty: ignore[unresolved-attribute]
            else:
                members = self.obj.variants()  # ty: ignore[unresolved-attribute]

            description += "\n\n" + "\n".join(f"- {k}" for k in members) + "\n\n"

        return description

    def get_see_also(self) -> str:
        """Return the object's See Also block.

        The block is rendered as an info admonition.

        Returns
        -------
        str
            Object's See Also block.

        """
        lines = self.get_block("See Also").splitlines()
        block = ""

        for line in lines:
            if line:
                if not block:
                    block = "<br>" + '\n!!! info "See Also"'

                cls = self.get_obj(line)
                summary = f"<div style='margin: -1em 0 0 1.2em'>{cls.get_summary()}</div>"

                # If it's a class, refer to the page, else to the anchor
                if cls._parent_anchor:
                    link = f"[{cls._parent_anchor}{cls.name}]"
                else:
                    link = ""

                block += f"\n    [{cls.name}]{link}<br>    {summary}\n"

        return block

    def get_block(self, block: str) -> str:
        """Return a block from the docstring.

        Parameters
        ----------
        block : str
            Name of the block to retrieve.

        Returns
        -------
        str
            Block in docstring.

        """
        pattern = f"(?<={block}\n{'-' * len(block)}).*?(?={'|'.join(self.blocks)})"
        match = re.search(pattern, self.doc, re.S)
        return match.group() if match else ""

    def get_table(self, blocks: list) -> str:
        """Return a table from one or multiple blocks.

        Parameters
        ----------
        blocks : list
            Blocks to create the table from.

        Returns
        -------
        str
            Table in html format.

        """
        table = ""

        for block in blocks:
            if isinstance(block, str):
                name = block.capitalize()
                config = {}
            else:
                name = next(iter(block)).capitalize()
                config = block[name.lower()]

            # Get from config which attributes to display
            if include := config.get("include"):
                attrs = include
            else:
                attrs = [
                    m
                    for m, _ in getmembers(self.obj, lambda x: not isroutine(x))
                    if not m.startswith("_")
                    and not any(re.fullmatch(p, m) for p in config.get("exclude", []))
                ]

            content = ""

            if not config.get("from_docstring", True):
                for attr in attrs:
                    if ":" in attr:
                        obj = AutoDocs.get_obj(attr).obj
                    else:
                        obj = getattr(self.obj, attr)

                    if isinstance(obj, property):
                        obj = obj.fget
                    elif obj.__class__.__name__ == "cached_property":
                        obj = obj.func  # ty: ignore[unresolved-attribute]

                    # Get the return type. Sometimes it returns a string 'Pandas'
                    # and sometimes a class pd.DataFrame. Unclear why
                    output = str(signature(obj).return_annotation)

                    header = f"{obj.__name__}: {types_conversion(output)}"
                    text = f"<div markdown class='param'>{getdoc(obj)}\n</div>\n\n"

                    anchor = f"[](){{#{self.name.lower()}-{obj.__name__}}}\n"
                    content += f"{anchor}<strong>{header}</strong><br>\n\n{text}"

            elif match := self.get_block(name):
                # Headers start with a letter, *, -, or [ after new line
                header_start = r"^[\[a-zA-Z*-].*?$"

                for header in re.findall(header_start, match, re.M):
                    # Check that the default value in docstring matches the real one
                    if default := re.search("(?<=default=).+?$", header):
                        try:
                            param = header.split(":")[0].strip()
                            real = signature(self.obj).parameters[param]

                            # String representation uses single quotes
                            default = str(default.group()).replace('"', "'")

                            # Remove quotes for string values
                            if default.startswith("'") and default.endswith("'"):
                                default = default[1:-1]

                            expected = str(real.default)
                            if isinstance(real.default, Enum):
                                expected = f"{type(real.default).__name__}.{real.default.name}"
                                default = re.sub(r"\[([^]]+)\]", r"\1", default)
                            if is_dataclass(self.obj):
                                item = next(
                                    field for field in fields(self.obj) if field.name == param
                                )
                                if item.default_factory is not MISSING:
                                    expected = f"{item.default_factory.__name__}()"

                            if real.default is not Ellipsis and default != expected:
                                raise ValueError(
                                    f"Default value {real.default} of parameter {param} "
                                    f"of object {self.obj} doesn't match the value "
                                    f"in the docstring: {default}.",
                                )
                        except KeyError:
                            pass

                    # Get the body corresponding to the header
                    pattern = rf"(?<={re.escape(header)}\n).*?(?=\n{header_start}|\Z)"
                    body = re.search(pattern, match, re.S | re.M).group()

                    header = header.replace("*", r"\*")  # Use literal * for args/kwargs
                    text = f"<div class='param' markdown='block'>{self.parse_body(body)}</div>\n\n"

                    # Only parameters and attributes have names (returns and yields don't)
                    if name in ("Parameters", "Attributes"):
                        obj_name = header.split(":")[0].strip()
                        anchor = f"[](){{#{self.name.lower()}-{obj_name}}}\n"
                    else:
                        anchor = ""

                    content += f"{anchor}<strong>{header}</strong><br>\n\n{text}"

            elif name == "Attributes" and is_dataclass(self.obj):
                for item in fields(self.obj):
                    annotation = types_conversion(str(item.type))

                    if item.default is not MISSING:
                        default = f"Default: {escape(repr(item.default))}."
                    elif item.default_factory is not MISSING:
                        factory = getattr(item.default_factory, "__name__", "factory")
                        default = f"Fresh value from {escape(factory)} for each instance."
                    else:
                        default = "Required constructor argument."

                    anchor = f"[](){{#{self.name.lower()}-{item.name}}}\n"
                    content += (
                        f"{anchor}<strong>{item.name}: {escape(annotation)}</strong><br>\n\n"
                        f"<div class='param' markdown>{default}</div>\n\n"
                    )

            if content:
                table += f"<tr markdown><td class='td_title'><strong>{name}</strong></td>"
                table += f"<td class='td_params' markdown='block'>{content}</td></tr>"

        if table:
            table = f"<table markdown class='table_params'>{table}</table>"

        return table

    def get_methods(self, config: dict) -> str:
        """Return an overview of the methods and their blocks.

        Parameters
        ----------
        config : dict
            Options to configure. Choose from:

            - toc_only: Whether to display only the toc.
            - solo_link: Whether the link comes from the parent.
            - include: Methods to include.
            - exclude: Methods to exclude.

        Returns
        -------
        str
            Toc and blocks for all selected methods.

        """
        toc_only = config.get("toc_only")
        solo_link = config.get("solo_link")
        include = config.get("include", [])
        exclude = config.get("exclude", [])

        if include:
            methods = include
        else:
            members = getmembers(
                self.obj,
                predicate=lambda f: ismethod(f) or isfunction(f) or isbuiltin(f) or isroutine(f),
            )

            methods = [
                m
                for m, _ in members
                if not m.startswith("_") and not any(re.fullmatch(p, m) for p in exclude)
            ]

        # Create toc
        toc = "<table markdown style='font-size: 0.9em'>"

        for method in methods:
            func = AutoDocs(self.obj, method=method)

            name = f"[`{method}`][{'' if solo_link else func._parent_anchor}{method.strip('_')}]"
            summary = func.get_summary()
            toc += f"<tr markdown><td markdown>{name}</td><td>{summary}</td></tr>"

        toc += "</table>"

        # Create methods
        blocks = ""

        if not toc_only:
            for method in methods:
                func = AutoDocs(self.obj, method=method)

                blocks += "<br>" + func.get_signature()
                blocks += func.get_summary() + "\n"

                if func.module.startswith("linescope"):
                    if description := func.get_description():
                        blocks += "\n\n" + description + "\n"

                if example := func.get_block("Examples"):
                    blocks += "!!! example" + "\n    ".join(example.split("\n")) + "\n\n"

                if table := func.get_table(["Parameters", "Returns", "Yields"]):
                    blocks += table + "<br>"

                if related := func.get_see_also():
                    blocks += related + "\n\n"

                if not table and not example:
                    # \n to exit markdown and <br> to insert space
                    blocks += "\n" + "<br>"

        return toc + blocks


# Functions ======================================================== >>


def render(markdown: str, **kwargs) -> str:  # noqa: ARG001
    """Render the Markdown page.

    This function is the landing point for the mkdocs-simple-hooks plugin,
    called in mkdocs.yml.

    Parameters
    ----------
    markdown : str
        Markdown source text of page.

    **kwargs
        Additional keyword arguments of the hook.
            - page: Mkdocs Page instance.
            - config: Global configuration object.
            - files: Global files collection.

    Returns
    -------
    str
        Modified markdown/html source text of page.

    """
    # Literal examples may describe directives without invoking them. Protect
    # fences before scanning, then restore them for the Markdown renderer.
    fences = []

    def protect_fence(match):
        fences.append(match.group())
        return f"\x00LINESCOPE_FENCE_{len(fences) - 1}\x00"

    markdown = FENCE_RE.sub(protect_fence, markdown)
    autodocs = None

    while match := re.search("(:: )([a-z].*?)(?=::|\n\n|\\Z)", markdown, re.S):
        command = yaml.safe_load(match.group(2))

        # Commands should always be dicts with the configuration as a list in values
        if isinstance(command, str):
            if ":" in command:
                autodocs = AutoDocs.get_obj(command)
                markdown = markdown[: match.start()] + markdown[match.end() :]
                continue
            else:
                command = {command: None}  # Has no options specified

        if autodocs:
            if "toc" in command:
                text = autodocs.get_toc()
            elif "signature" in command:
                text = autodocs.get_signature()
            elif "head" in command:
                text = autodocs.get_summary() + "\n\n" + autodocs.get_description()
            elif "summary" in command:
                text = autodocs.get_summary()
            elif "description" in command:
                text = autodocs.get_description()
            elif "table" in command:
                text = autodocs.get_table(command["table"])  # ty: ignore[invalid-argument-type]
            elif "see also" in command:
                text = autodocs.get_see_also()
            elif "references" in command:
                text = autodocs.get_block("References")
            elif "examples" in command:
                text = autodocs.get_block("Examples") + "\n\n"
            elif "methods" in command:
                text = autodocs.get_methods(command["methods"] or {})
            else:
                text = ""

            markdown = markdown[: match.start()] + text + markdown[match.end() :]

            # Change the custom autorefs now to use [...][self-...]
            markdown = custom_autorefs(markdown, autodocs)

    markdown = custom_autorefs(markdown)

    for index, fence in enumerate(fences):
        markdown = markdown.replace(f"\x00LINESCOPE_FENCE_{index}\x00", fence)

    return markdown


def types_conversion(dtype: str) -> str:
    """Convert data types to a clean representation.

    Parameters
    ----------
    dtype : str
        Type to convert.

    Returns
    -------
    str
        Converted type.

    """
    types = {
        "<class '": "",
        "'>": "",
        "typing.": "",  # For typing.Any
    }

    for k, v in types.items():
        dtype = dtype.replace(k, v)

    dtype = dtype.replace("linescope.model.", "")
    pattern = r"\b(" + "|".join(sorted(LINKED_TYPES)) + r")\b"
    dtype = re.sub(pattern, r"[\1]", dtype)

    return dtype


def corrections(html: str, **kwargs) -> str:  # noqa: ARG001
    """Make last minute corrections to the page.

    This function adjusts the url to the download sources and changes
    the size of plotly plots to fit the screen's width.

    Parameters
    ----------
    html : str
        HTML source text of page.

    **kwargs
        Additional keyword arguments of the hook.
            - page: Mkdocs Page instance.
            - config: Global configuration object.
            - files: Global files collection.

    Returns
    -------
    str
        Modified html source text of page.

    """
    # Swap url to example datasets
    html = html.replace("./datasets/", "docs_source/examples/datasets/")

    # Correct sizes of the plot to adjust to frame
    html = re.sub(r'(?<=style="height:\d+?px; width:)\d+?px(?=;")', "100%", html)
    html = re.sub(r'(?<="showlegend":\w+?),"width":\d+?,"height":\d+?(?=[},])', "", html)

    # Execution guards belong to the documentation build and must never be visible to readers.
    html = html.replace("# norun", "")

    return html


def clean_search(config: MkDocsConfig):
    """Clean the search index.

    Remove unnecessary plotly and css blocks (from mkdocs-jupyter) to keep
    the search index small.

    Parameters
    ----------
    config : MkdocsConfig
        Object containing the search index.

    """
    with open(f"{config.data['site_dir']}/search/search_index.json") as f:
        search = json.load(f)

    for elem in search["docs"]:
        # Remove plotly graphs
        elem["text"] = re.sub(r"window\.PLOTLYENV.*?\)\s*?}\s*?", "", elem["text"], flags=re.S)

        # Remove mkdocs-jupyter css
        elem["text"] = re.sub(
            r"\(function \(global, factory.*?(?=Example:)",
            "",
            elem["text"],
            flags=re.S,
        )

    with open(f"{config.data['site_dir']}/search/search_index.json", "w") as f:
        json.dump(search, f)


def custom_autorefs(markdown: str, autodocs: AutoDocs | None = None) -> str:
    """Handle autorefs links.

    The documentation accepts some custom formatting for autorefs
    links in order to make the documentation cleaner, easier to write. The
    custom
    transformations are:

    - Replace single square brackets with [anchor][anchor].
    - Replace keywords with full url (registered in CUSTOM_URLS).
    - Replace keyword `self` with the name of the class.
    - Replace spaces with dashes.
    - Convert all links to lower case.

    Parameters
    ----------
    markdown : str
        Markdown source text of page.

    autodocs : [AutoDocs] | None, default=None
        Class for which the page is created.

    Returns
    -------
    str
        Modified source text of page.

    """
    result, start = "", 0

    # References belong to prose. Leave literal code and explicit links intact,
    # including square brackets used by Python's container annotations.
    def masker(text: str) -> str:
        pattern = r"```.*?```|`[^`\n]*`|!?\[[^\]\n]*\]\([^\n]*?\)"
        return re.sub(pattern, lambda match: " " * len(match.group()), text, flags=re.S)

    # Skip regex check for very long docs
    if len(markdown) < 1e5:
        for match in re.finditer(LINK_RE, masker(markdown)):
            # The mask locates prose references while preserving offsets.
            # Read their labels from the original text so inline code stays
            # visible in method tables such as [`start`][session-start].
            anchor = markdown[match.start(1) : match.end(1)]
            link = markdown[match.start(2) : match.end(2)] if match.group(2) else None

            # Callable[[str], bool] uses a list of argument types, not a
            # Markdown reference. Built-in scalar names remain literal here.
            if anchor in {"str", "int", "float", "bool", "bytes", "object", "Any", "None"}:
                continue

            # Numeric references, reference definitions, and image labels use
            # Markdown's own resolver rather than the API symbol registry.
            if anchor.isdigit() or anchor.startswith("^"):
                continue

            if markdown[match.end() :].startswith(":"):
                continue

            if match.start() and markdown[match.start() - 1] == "!":
                continue

            text = markdown[match.start() : match.end()]

            if not link and anchor != "source":
                # Only adapt when has form [anchor] (no second square brackets pair)
                link = re.sub(r"[.'`]", "", anchor).replace(" ", "-").lower()
                text = f"[{anchor}][{link}]"

            if link in CUSTOM_URLS:
                # Replace keyword with custom url
                text = f"[{anchor}]({CUSTOM_URLS[link]})"

            if link and "self" in link and autodocs:
                link = link.replace("self", autodocs.obj.__name__.lower())
                text = f"[{anchor}][{link}]"

            result += markdown[start : match.start()] + text
            start = match.end()

    return result + markdown[start:]
