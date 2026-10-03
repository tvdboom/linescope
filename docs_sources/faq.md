# Frequently asked questions

## Why does LineScope default to an optional dependency?

Scalene is the preferred sampling engine, but its platform requirements should not prevent
installation of the source browser, notebook adapters, or trace collector. Install the extra
where supported, or explicitly choose `backend="trace"`.

## Does a zero time mean a line never ran?

No. Sampling may miss short lines. Hit counts are only shown when recorded by the backend;
sample counts are not execution counts. Unavailable measurements use a dash.

## Why can a Spark task total exceed elapsed time?

Tasks execute concurrently. Summed executor/task time is cumulative work, while wall time is
elapsed time observed by the driver. The report labels these separately.

## Does it profile Python UDFs on Spark executors?

The initial release profiles driver Python and available Spark execution context. Separate
executor Python processes need their own collector and correlation, which is future scope.

## Does a child Databricks notebook get deep profiling automatically?

The parent records the wait and child relationship. Separate child line measurements require
child instrumentation and explicit result transport/merging; the parent cannot trace another job.

## Is a Spark cluster required for tests?

No. Unit tests use controlled fakes. Optional integration tests start local Spark on one machine
with Java and generated data. Databricks runtime validation is a separate manual check.

## Can I open reports offline?

Yes. Report HTML contains its own assets and source snapshots. Review those snapshots before
sharing because they may include private source or paths.

## Are Tachyon and attach mode implemented?

Not in the initial release. The backend protocol and optional metrics leave room for future
collectors without coupling the report to one engine.
