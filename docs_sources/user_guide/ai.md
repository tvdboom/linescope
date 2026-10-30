# AI suggestions

Use the lightning button beside a source line for a quick performance
suggestion. It sends the same captured source, related definitions, profiling
measurements, and workload constraints as the detailed review through your
saved connection. Configure a provider once if none is saved.

The response contains a small code change and at most four short explanation
lines. A change may include nearby lines or required imports when a single
line is insufficient. If no safe performance improvement is supported, or the
suggestion repeats the current code, the popup shows **No performance gain to
update.** Suggestions are not applied to your source.

Click a source row or line number to select it. Drag across rows to select a
block, Shift-click to extend a range, or Ctrl-click (Cmd-click on macOS) to
toggle individual lines. Ctrl/Cmd-drag adds or removes a block. Hold Alt while
dragging to copy source text instead. Symbol links still navigate normally.

The selection toolbar uses the same icons and order as the row actions:
sparkles for a detailed review, then lightning for a quick code suggestion.
The close icon or Escape clears the selection. Disjoint selections retain
their exact line numbers. A selection stays within one source snapshot;
notebook cells and revisions remain separate.

Select the AI icon beside a source line or on a Spark view. Choose a provider
in settings and save. A model is selected automatically; the dropdown lets you
change it. While models load, the dropdown and refresh button are disabled,
and the refresh arrows rotate in place.

Describe your workload and constraints, then select **Generate suggestion**.
The segmented goal control defaults to **Performance**; choose **Memory** or
**Both** to change the priority.
Context updates automatically as you change the goal, constraints, or included
files. While updating, the **Context sent** control shows a spinner and
**Updating context…** in place. The old preview is cleared and cannot be opened
until the new context is ready. Expand **Context sent** to review or edit it
before generating. Suggestions appear in the report and are not applied to
your code.

## Use your Codex subscription

From a source checkout, regenerate and open the bundled demo in one step:

```console
uv run python examples/script_example.py --ai-agent codex
```

This reruns the workload and replaces `.cache/linescope-demo.html` with the
current report interface. Keep the terminal running while using suggestions.
The `--serve` option below opens an existing HTML snapshot; it does not
regenerate its interface after source changes.

Install the [Codex CLI][codex-cli], then run on the computer serving the report:

```console
codex login
codex login status
linescope --serve report.html --ai-agent codex
```

Sign in with the ChatGPT account that has Codex access. LineScope selects
**Codex** automatically. Leave **Credential** blank to use that login and keep
**Default** to use the CLI's model setting.

On Windows, LineScope can also use the native CLI bundled with the ChatGPT
desktop app when it is not on your terminal's PATH.

The **OpenAI** provider uses API keys and API billing. Subscription login goes
through **Codex** and needs the local viewer. See [Codex authentication][auth].

## API providers

| Provider | Credential |
| --- | --- |
| Claude | Anthropic API key |
| OpenAI | OpenAI API key |
| Custom API | Key required by your service, if any |

Models load after you enter a credential. A custom API also needs its full
Chat Completions URL, such as `http://127.0.0.1:11434/v1/chat/completions`.
Remote endpoints require HTTPS.

If browser requests are blocked, use the local viewer:

```console
linescope --serve report.html
```

With **Credential** blank, the viewer reads `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, or `LINESCOPE_AI_API_KEY` for the selected provider.

## Other local agents

Install and sign in to the CLI before starting the viewer:

```console
linescope --serve report.html --ai-agent claude
linescope --serve report.html --ai-agent copilot
```

Keep **Default** or choose a model. Claude Code offers Opus, Sonnet, and Haiku.
The presets disable execution tools.

Custom adapters read a UTF-8 prompt from stdin and return text on stdout:

```console
linescope --serve report.html --ai-command my-agent --ai-arg=--noninteractive
```

Adapters run in a temporary directory; use absolute script paths. On Windows,
use a native executable or Python wrapper. Stop the viewer with `Ctrl+C`.

## Spark context

Suggestions include captured plans, operator metrics and units, action counters,
stages, and job IDs. A line includes its correlated actions; an action page
reviews that action; the Spark overview compares all actions.

The prompt prefers the final AQE plan and separates driver time from task work.
Large contexts mark omissions. No extra Spark actions run and no result datasets
are collected.

## Context and privacy

Use the detailed review to inspect code and plans before sending them.
Notebook parameters and arbitrary
run metadata are excluded. **Generate suggestion** sends profile context;
loading models sends only connection settings.

Entered keys stay in the tab's memory, not HTML or browser storage. The viewer
binds to `127.0.0.1` with a temporary session token. Regenerate older reports to
get the current AI controls.

[codex-cli]: https://learn.chatgpt.com/docs/cli
[auth]: https://learn.chatgpt.com/docs/auth
