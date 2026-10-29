# AI suggestions

Select the AI icon beside a source line or on a Spark view. Choose a provider
in settings and save. A model is selected automatically; the dropdown lets you
change it.

Describe your workload and constraints, review **Context sent to the AI**, then
select **Generate suggestion**. Suggestions appear in the report and are not
applied to your code.

## Use your Codex subscription

Install the [Codex CLI][codex-cli], then run on the computer serving the report:

```console
codex login
codex login status
linescope --serve report.html --ai-agent codex
```

Sign in with the ChatGPT account that has Codex access. LineScope selects
**Codex** automatically. Leave **Credential** blank to use that login and keep
**Default** to use the CLI's model setting.

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

Review code and plans before sending them. Notebook parameters and arbitrary
run metadata are excluded. **Generate suggestion** sends profile context;
loading models sends only connection settings.

Entered keys stay in the tab's memory, not HTML or browser storage. The viewer
binds to `127.0.0.1` with a temporary session token. Regenerate older reports to
get the current AI controls.

[codex-cli]: https://learn.chatgpt.com/docs/cli
[auth]: https://learn.chatgpt.com/docs/auth
