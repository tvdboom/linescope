# Reports

Start with **Overview** to find expensive lines, then follow a source link to
inspect the surrounding code. Reports store source snapshots, so you can still
read the profiled version after the original files change.

![Report overview with the most expensive lines](../img/report.jpg)

## The main views

| View | Use it to |
| --- | --- |
| Overview | Find the lines worth investigating first |
| Files | Browse files, notebooks, and child notebook invocations |
| Functions | Compare function time and jump to definitions |
| Memory | Follow process RAM growth and inspect active source lines |
| GPU | Compare sampled device work and memory with driver time |
| Spark | Inspect actions, query plans, and distributed metrics |

Memory and GPU views depend on the enabled collectors. Spark appears when
executions have been captured, including those in child notebooks.

### Sorting and heatmaps

Select a column heading to sort; select it again to reverse the order. Unknown
measurements stay last. In source tables, select **Source** to restore line
order. Notebook sorting stays within each cell.

Use **Heatmap by** to highlight **Time** or, with memory collection enabled,
positive **Mem Growth**. Select **None** to turn it off. Sorting and heatmaps
work independently.

## AI suggestions

AI suggestions work directly from a saved report in your browser. Hover over
a line in the overview or a source file to reveal its AI icon. The icon is
also available through keyboard focus and on touch screens. If a provider is
not configured yet, selecting the icon opens settings. The header's AI icon
opens the same settings at any time.

```console
linescope --backend trace --memory --display none -o report.html application.py
```

Open the saved `report.html` in your browser. The optional local viewer also
supports native agents and credentials from environment variables:

```console
linescope --serve report.html
```

Choose a **Model provider**, model, and credential, then save settings.
Suggestions become ready automatically when the required settings are
present. Every provider uses this same flow, with no preferred vendor. The
viewer selects the sole configured provider, or the agent explicitly chosen
at startup. When several API providers are configured, choose which one to
use. APIs also require a model.

Select a line's AI icon, choose runtime, memory, or both, and describe the
workload or constraints. Review the generated context, then select
**Generate suggestion**. The answer appears inside the report; LineScope does
not apply changes or run the suggested code.

### Choose a model provider

Saved reports offer **Claude**, **OpenAI**, and **Custom API** in the
**Model provider** menu. A local agent also appears when it is configured in
the viewer. Credentials belong to the selected provider; changing providers
clears a credential entered for the previous provider.

Claude and OpenAI include their endpoint presets; no URL is required. Enter
an available model ID and API key. Claude uses the Anthropic Messages API;
OpenAI uses the Responses API with storage disabled.

Only **Custom API** asks for a URL. Use a service, remote agent, or local model
server that accepts the Chat Completions request and response format.
Supply its complete Chat Completions endpoint, such as
`http://127.0.0.1:11434/v1/chat/completions`, and an installed model ID.
Authentication can be omitted for local services that do not require it;
otherwise enter a key. Remote endpoints require HTTPS. These APIs use the
same model, credential, and suggestion controls as configured local agents.

Saved reports send requests directly from the browser to the selected API.
The provider must allow requests from that browser origin through CORS. If it
blocks the request, open the optional local viewer with
`linescope --serve report.html`; the viewer makes the API request instead.
The viewer can also use `ANTHROPIC_API_KEY` for Claude, `OPENAI_API_KEY` for
OpenAI, or `LINESCOPE_AI_API_KEY` for a custom API when the credential field
is blank. A standalone HTML file cannot read your environment variables.

### Configure an agent

Local Claude Code, Codex, Copilot, and custom command adapters require the
optional viewer because a saved HTML file cannot start processes. Install
the agent's native CLI and choose it when starting the viewer:

```console
linescope --serve report.html --ai-agent claude
linescope --serve report.html --ai-agent codex
linescope --serve report.html --ai-agent copilot
```

The chosen agent appears in **Model provider** and is selected automatically.
Use its existing login or enter a credential in AI settings. Request keys
are passed only in the child process environment: `ANTHROPIC_API_KEY` for
Claude, `CODEX_API_KEY` for Codex, and `COPILOT_GITHUB_TOKEN` for Copilot. You
can also set that variable before starting the viewer. A blank credential
preserves existing authentication. The optional **Model** field overrides the
agent's model for that request; leaving it blank keeps the agent's own choice.

Claude runs without tools, customizations, or saved sessions. Codex uses an
ephemeral read-only session with execution tools disabled. Copilot also runs
with tools disabled. These presets receive the assembled prompt and return
their suggestions directly to the report without editing the project.

For another agent, configure an executable that reads a UTF-8 prompt from
stdin and writes its suggestion as UTF-8 text to stdout:

```console
linescope --serve report.html --ai-command my-agent --ai-arg=--noninteractive
```

Repeat `--ai-arg` for each literal argument. Commands run without shell
expansion in a temporary directory, using the captured report context.
Use absolute paths for any script or configuration arguments, since relative
arguments are resolved inside that temporary directory.
The **Custom agent** provider uses the same credential and model fields.
A supplied credential becomes `LINESCOPE_AI_API_KEY` in the child environment;
a supplied model becomes `LINESCOPE_AI_MODEL`. Neither changes the parent
environment or the configured command's arguments. A command adapter can
connect agents with a different SDK or protocol through the viewer. The
adapter controls its own authentication and permissions; configure it to
return suggestions without editing files and to clean up any detached child
processes it starts. On Windows, use a native executable or a Python wrapper
instead of a `.cmd` or `.bat` launcher. Agent requests time out after three
minutes, and temporary input and output are removed.

### Context and privacy

Prompts include captured source around the selected line, relevant resolved
project relationships, and the available line measurements. They distinguish
unknown values from zero, sampled observations from traced hits, retained
Python allocations from process memory, and driver wall time from cumulative
Spark task costs. Context is bounded, with omissions identified in the prompt.
Suggestions ask for evidence, a small proposed change, tradeoffs, and a
validation plan rather than invented performance gains.

Only **Generate suggestion** sends context to the selected provider or agent.
Reports contain source snapshots; inspect the preview for sensitive source
before sending it. Arbitrary notebook parameter metadata is excluded from AI
context. API keys entered in either a saved report or the viewer stay in the
current tab's memory and request. They are never embedded in the saved report
or browser storage, and must be entered again after reopening the report.
Local agents retain their own authentication and history policies.

The viewer binds only to `127.0.0.1` and protects requests with a temporary
session token. Stop it with `Ctrl+C`; use `--no-open-browser` to print the URL
without launching a browser, or `--port` to select a local port. Ordinary saved
reports continue to work offline. Older reports need to be regenerated to
include the captured AI context.

Provider references: [Claude CLI reference][claude],
[Codex noninteractive mode][codex-exec], [Copilot CLI reference][copilot],
and [OpenAI text generation][openai-text].

[claude]: https://code.claude.com/docs/en/cli-reference
[openai-text]: https://developers.openai.com/api/docs/guides/text
[codex-exec]: https://learn.chatgpt.com/docs/non-interactive-mode
[copilot]: https://docs.github.com/en/copilot/reference/cli-command-reference

## Report header

The header shows the run status, backend, and collection method. Select a value
to open its guide page; these help links need an internet connection.

### Run status

`Success` means no failure was recorded. `Failed` reports retain measurements
collected before the error. Notebook entries marked `Reference` represent
inline execution that belongs to their parent run.

### Backend collector

The backend identifies the collector: `trace`, `scalene`, `tachyon`, or a
[custom backend](backends.md#custom-backends).

### Collection method

`Tracing` records execution events. `Sampling` estimates time from periodic
observations. `Mixed collection` combines runs with different collection
capabilities. See [Backends](backends.md) for their tradeoffs.

## Profiling scope

Elapsed wall time includes waits during the session. It does not total CPU time
across workers. Thread coverage depends on the
[backend](backends.md#profiling-scope); separate worker processes are not
profiled automatically.

From one minute onward, the overview shows elapsed wall time in minutes and
seconds with lowercase suffixes: 292 seconds appears as `4m 52s`. Fractional
seconds use two decimal places, such as `4m 52.13s`; `.00` is omitted.

Spark reports keep driver waits separate from cumulative task metrics, which
can overlap. See [Spark timing](spark.md#worker-scope-and-overall-time).

## Line columns

| Column | Meaning |
| --- | --- |
| Time | Time attributed to the line; estimated by sampling |
| Samples | Observations of the line, rather than execution counts |
| Hits | Recorded executions, when supported |
| Avg / hit | Time divided by hits |
| Mem Change | Accumulated process-memory change during the line's intervals |
| Peak Mem | Highest observed process memory during the line |
| GPU time | Estimated device work, separate from driver wall time |
| GPU peak memory | Highest sampled device-memory value for the line |
| Context | Links to Spark executions or child notebook invocations |
| Source | Captured source text |

Only supported columns appear. A dash means unavailable or unmeasured; zero
samples do not prove that a line never ran. Sampling reports cannot provide
hit counts or per-hit averages. The reported sampling rate is the main run's
observed samples divided by its elapsed time.

Function time sums the function's own lines, excluding other project functions
it calls. **Lines** counts the definition and body, including comments, blank
lines, and nested definitions, whether they executed or not.

### Memory

With `memory=True`, source tables show process RAM measurements. The **Memory**
view shows the observed peak, largest accumulated line change, timeline, and
growth ranking. Use the ranking to find lines that retain RAM over repeated
iterations.

Hover over the timeline to inspect a reading and follow its source link when
available. You can also focus the plot, use arrow keys to move between readings,
and press Enter to open linked source. A linked line was active at the reading;
it does not necessarily own the allocations.

Retained Python allocation changes are available in the profile data and
compact cell summaries. They measure something different from process RAM.
Child processes have separate timelines. See
[memory collection](backends.md#memory) for scope and peak limitations.

## Source snapshots

The report includes project files and captured notebook cells, selected through
[configuration](configuration.md). Third-party internals stay outside the source
browser, but their cost can contribute to the calling project line.

Each notebook appears once in **Files**, with executed cells in notebook order
and their original cell numbers. Line numbers restart in each cell. Unknown
notebook positions use execution or frontend ID labels. Unexecuted cells and
generated magic wrappers are omitted. Earlier definition cells appear when
their code executes during collection.

Source links target the exact cell and line. If a cell changes during
collection, its executed revisions remain available as **version 1**,
**version 2**, and so on. Source-only child notebook fallbacks can include the
exported notebook when execution observation is unavailable; their metrics
remain unavailable.

## Source navigation

![Source timing, heatmap rows, and function links](../img/source.jpg)

Select an underlined function or class to jump to its definition. Calls on the
same line have separate links. Dynamic dispatch, ambiguous definitions, and
third-party calls may remain unlinked.

Spark and child notebook links open their captured context. Use browser back
and forward to retrace navigation.

## Saving and sharing

Call `session.save("report.html")` to write a self-contained report. It embeds
source and assets and works offline without the original project or a server.

Review source snapshots, paths, and integration metadata before sharing.
Notebook cells can contain credentials or private data; HTML escaping does not
remove them.
