# Introduction

LineScope combines measurements and source context. A backend records timing or allocation
information, then LineScope discovers project-owned sources, snapshots them, resolves local
symbols, correlates integrations, and writes an HTML report.

## The main views

| View | Question it answers |
| --- | --- |
| Overview | Where should I start investigating? |
| Files | Which lines in this source file consumed time? |
| Functions | Where is a function defined, and how much time was attributed to it? |
| Notebooks | Which captured cells and child notebooks belong to this run? |
| Spark | Which action ran, and what did Spark execute? |

## Three kinds of information

**Measured** values come from the backend or runtime. **Derived** values, such as a function
summary, aggregate measurements. **References** explain relationships, such as a source line
participating in a Spark plan. A reference is not a claim that Spark spent an exact amount of
wall time on that transformation.

Missing hits, memory, or plan metrics remain unavailable. In particular, a sample count is not
an execution count. A very fast line may receive no samples while still having executed.
