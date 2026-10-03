# Source and navigation

Project discovery uses the configured root, nearby project metadata, and explicit include/exclude
rules. Installed third-party libraries, interpreter internals, and unrelated files remain outside
the source browser. Their work can still contribute to the calling project's measured time.

## Snapshots

Source is stored with the profile result, including notebook cells. Editing or deleting the
original file later does not change the saved report. A source unit has an identifier, original
path, text, and kind (`python` or `notebook`).

## Symbol resolution

Static analysis builds an index of project functions, classes, methods, imports, and call sites.
The renderer links individual symbol spans. Multiple calls on one line remain separate targets.
Known local class instances can resolve methods; dynamic dispatch, runtime imports, and ambiguous
names may remain unlinked. An absent link is preferable to a misleading destination.

Includes and excludes are scope controls, not a mechanism for obtaining private code from
inaccessible locations. Notebook references use captured or explicitly registered source.

## Sharing

Generated HTML embeds source and metadata to work offline. Review those snapshots before sharing
outside your project, especially notebook cells containing inline credentials or private paths.
Escaping prevents source text from becoming executable HTML; it does not remove sensitive source.
