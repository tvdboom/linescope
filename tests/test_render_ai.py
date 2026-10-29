"""LineScope.

Author: Mavs
Description: Verify optional line actions and inert AI report context.

"""

import json

import pytest

from linescope.ai.connections import browser_connections
from linescope.model import (
    BackendCapabilities,
    LineStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
    SparkExecution,
)
from linescope.render.html import render_html
from tests.test_render import ReportDOM


def _profile() -> ProfileResult:
    """Build source snapshots with measured and unavailable line costs.

    Preserve separate notebook revisions so action targets cannot collapse
    distinct captured versions of a cell onto the same line identity.

    Returns
    -------
    ProfileResult
        A Python file and two notebook revisions with shared display labels.

    """
    units = [
        SourceUnit("python", "worker.py", "items = []\nitems.append(1)\n# unused\n"),
        SourceUnit("cell-v1", "notebook.py", "value = 1\n", "notebook", cell_number=1),
        SourceUnit("cell-v2", "notebook.py", "value = 2\n", "notebook", cell_number=1),
    ]
    return ProfileResult(
        ProfileRun(
            lines=[
                LineStats(
                    SourceLocation("python", 2),
                    wall_time_ns=1200,
                    hits=1,
                    ram=ProcessMemoryStats(delta_bytes=-12, peak_bytes=500),
                ),
                LineStats(SourceLocation("cell-v1", 1), wall_time_ns=800),
                LineStats(SourceLocation("cell-v2", 1), wall_time_ns=600),
            ]
        ),
        {unit.id: unit for unit in units},
        "trace",
        BackendCapabilities(memory=True),
    )


def test_every_source_line_and_overview_action_preserve_exact_snapshot_identity():
    """Target captured notebook revisions and unmeasured source independently.

    Inspect real button attributes while preserving existing source text and
    summary table columns used by sorting and report navigation.

    """
    document = ReportDOM(render_html(_profile())).root
    context = json.loads(document.find_all("script", id="linescope-ai-context")[0].text())
    identities = context["source_anchors"]
    rows = document.find_all("tr", css="source-row")
    buttons = [row.find_all("button", css="ai-line-button")[0] for row in rows]
    assert {
        (identities[button.attributes["data-ai-source"]], button.attributes["data-ai-line"])
        for button in buttons
    } == {("python", "1"), ("python", "2"), ("python", "3"), ("cell-v1", "1"), ("cell-v2", "1")}
    for row, button in zip(rows, buttons, strict=True):
        assert button.attributes["type"] == "button"
        assert button.attributes["aria-label"].startswith("Ask AI to improve ")
        assert row.find_all("td", css="source-code")[0].text() == row.find_all("code")[0].text()
    overview = document.find_all("section", id="overview")[0]
    assert {
        (identities[button.attributes["data-ai-source"]], button.attributes["data-ai-line"])
        for button in overview.find_all("button", css="ai-line-button")
    } == {("python", "2"), ("cell-v1", "1"), ("cell-v2", "1")}


def test_ai_payload_preserves_metrics_and_cannot_escape_its_inert_script():
    """Retain raw measurements and hostile source safely in embedded JSON.

    JSON text must remain parseable without creating a script, image, or
    event-handler attribute from a source identity or literal.

    """
    profile = _profile()
    hostile = '</script><script>alert("source")</script><img src=x onerror=bad>&'
    unit = SourceUnit(hostile, hostile, f"text = {hostile!r}\n")
    profile.sources[unit.id] = unit
    document = ReportDOM(render_html(profile)).root
    payload = document.find_all("script", id="linescope-ai-context")[0]
    context = json.loads(payload.text())
    assert context["sources"][hostile]["source"] == unit.source
    line = context["runs"][0]["lines"][0]
    assert line["wall_time_ns"] == 1200
    assert line["ram"]["delta_bytes"] == -12
    assert line["ram"]["peak_bytes"] == 500
    assert line["memory"] is None
    assert line["samples"] is None
    assert "<" not in payload.text()
    assert "&" not in payload.text()
    assert len(document.find_all("script")) == 3
    assert len(document.find_all("img")) == 1
    assert not document.find_all(onerror="bad")
    anchor = next(
        key for key, identity in context["source_anchors"].items() if identity == hostile
    )
    assert document.find_all("button", **{"data-ai-source": anchor})
    assert not document.find_all("button", **{"data-ai-source": hostile})


def test_static_ai_connections_are_unselected_and_credentials_have_no_saved_value(
    monkeypatch: pytest.MonkeyPatch,
):
    """Embed usable API presets without copying the generator's credentials.

    Saved reports allow connecting from the browser. Only the local viewer
    injects authentication state; generating a report never configures an
    authenticated connection from the generator's environment.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Temporary environment changes restored after the test.

    """
    monkeypatch.setenv("OPENAI_API_KEY", "generator-openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "generator-claude-secret")
    monkeypatch.setenv("LINESCOPE_AI_API_KEY", "generator-custom-secret")
    rendered = render_html(_profile())
    assert "generator-openai-secret" not in rendered
    assert "generator-claude-secret" not in rendered
    assert "generator-custom-secret" not in rendered
    document = ReportDOM(rendered).root
    assert document.find_all("button", id="ai-settings-toggle")[0].attributes["data-ai-ready"] == (
        "false"
    )
    assert not document.find_all("input", id="ai-enabled")
    assert "ai-enabled" not in document.find_all("body")[0].attributes.get("class", "")
    key = document.find_all("input", id="ai-key")[0]
    assert key.attributes["type"] == "password"
    assert key.attributes["autocomplete"] == "off"
    assert "value" not in key.attributes
    assert not document.find_all("meta", name="linescope-ai-token")
    assert not document.find_all("meta", name="linescope-ai-connections")
    catalog = json.loads(
        document.find_all("meta", name="linescope-ai-catalog")[0].attributes["content"]
    )
    assert catalog == browser_connections()
    assert catalog["default_connection"] is None
    assert not any(connection["configured"] for connection in catalog["connections"])
    assert all(
        connection["available"] == (connection["kind"] == "api")
        for connection in catalog["connections"]
    )
    providers = document.find_all("select", id="ai-provider")[0]
    assert [
        option.attributes["value"]
        for option in providers.find_all("option")
        if "selected" in option.attributes
    ] == [""]
    assert document.find_all("button", id="ai-generate")[0].attributes["disabled"] is None
    assert document.find_all("p", id="ai-suggestion-status")[0].attributes["aria-live"] == "polite"
    csp = document.find_all("meta", **{"http-equiv": "Content-Security-Policy"})[0]
    assert "connect-src 'self' https: http:;" in csp.attributes["content"]


def test_ai_connection_fields_are_shared_by_agents_and_model_apis():
    """Keep model and credential entry independent of provider protocol.

    Every named agent and model API uses the same connection form. Only the
    endpoint field belongs to the custom API portion of the form.

    """
    document = ReportDOM(render_html(_profile())).root
    connection = document.find_all("form", id="ai-settings-form")[0]
    assert not connection.find_all("fieldset")
    assert not connection.find_all("input", type="checkbox")
    assert not connection.find_all(id="ai-setup")
    api_fields = connection.find_all("div", id="ai-api-fields")[0]
    assert [entry.attributes["id"] for entry in api_fields.find_all("input")] == ["ai-endpoint"]
    assert connection.find_all("select", id="ai-model")
    assert connection.find_all("input", id="ai-key")
    assert [entry.attributes["id"] for entry in connection.find_all("input")] == [
        "ai-endpoint",
        "ai-key",
    ]
    assert connection.find_all("label", **{"for": "ai-provider"})[0].text() == "Model provider"
    assert connection.find_all("label", **{"for": "ai-model"})[0].text() == "Model"
    assert connection.find_all("label", **{"for": "ai-key"})[0].text() == "Credential"
    assert "enabled" not in document.find_all("body")[0].attributes
    assert "A configured credential enables" not in connection.text()
    assert "You choose when to send" not in connection.text()


def test_header_and_source_actions_share_an_accessible_icon_without_text():
    """Use the same decorative mark without polluting source or table text.

    The settings button opens a dialog; its readiness indicator is not a
    toggle. Row actions retain their precise accessible source labels.

    """
    document = ReportDOM(render_html(_profile())).root
    header = document.find_all("button", id="ai-settings-toggle")[0]
    assert header.attributes["aria-haspopup"] == "dialog"
    assert "aria-pressed" not in header.attributes
    assert header.attributes["title"]
    assert header.text() == ""
    header_icon = header.find_all("svg", css="ai-icon")[0]
    assert header_icon.attributes["aria-hidden"] == "true"
    assert header_icon.attributes["focusable"] == "false"
    paths = [path.attributes for path in header_icon.find_all("path")]
    for button in document.find_all("button", css="ai-line-button"):
        assert button.text() == ""
        assert button.attributes["aria-label"].startswith("Ask AI to improve ")
        icon = button.find_all("svg", css="ai-icon")[0]
        assert icon.attributes == header_icon.attributes
        assert [path.attributes for path in icon.find_all("path")] == paths


def test_missing_source_has_no_ai_action_in_overview():
    """Avoid offering suggestions against a source absent from the report.

    Measurements may outlive unavailable source, but the AI action requires
    an exact captured snapshot instead of a guessed current file.

    """
    profile = _profile()
    profile.root_run.lines.append(LineStats(SourceLocation("missing", 1), wall_time_ns=5000))
    document = ReportDOM(render_html(profile)).root
    overview = document.find_all("section", id="overview")[0]
    assert len(overview.find_all("button", css="ai-line-button")) == 3


def test_ai_targets_keep_absolute_paths_out_of_visible_markup():
    """Keep opaque action identities while recovering exact paths in context.

    POSIX source paths are escaped within inert JSON and never repeated in
    button attributes or source labels.

    """
    path = "/project/private/main.py"
    unit = SourceUnit(path, path, "value = 1\n")
    profile = ProfileResult(ProfileRun(), {path: unit}, "trace", BackendCapabilities())
    html = render_html(profile)
    assert path not in html
    document = ReportDOM(html).root
    payload = json.loads(document.find_all("script", id="linescope-ai-context")[0].text())
    button = document.find_all("button", css="ai-line-button")[0]
    assert payload["source_anchors"][button.attributes["data-ai-source"]] == path
    assert payload["sources"][path]["path"] == path
    assert button.attributes["data-ai-label"] == "main.py:1"


def test_spark_actions_have_ai_targets_even_without_source_correlation():
    """Offer overview, summary-row, and action-page Spark suggestions.

    Use opaque action targets and escape hostile labels without requiring
    a captured source line for a Spark review.

    """
    profile = _profile()
    profile.root_run.spark_executions = [SparkExecution('action"<', name='<join & "scan">')]
    document = ReportDOM(render_html(profile)).root
    context = json.loads(document.find_all("script", id="linescope-ai-context")[0].text())
    buttons = document.find_all("button", css="ai-spark-button")
    assert len(buttons) == 3
    overview = next(button for button in buttons if "data-ai-execution" not in button.attributes)
    assert overview.attributes["data-ai-label"] == "Spark overview"
    for button in buttons:
        assert button.attributes["data-ai-scope"] == "spark"
        assert button.find_all("svg", css="ai-icon")
        assert button.attributes["type"] == "button"
        if "data-ai-execution" in button.attributes:
            assert context["spark_anchors"][button.attributes["data-ai-execution"]] == 'action"<'
            assert button.attributes["data-ai-label"] == '<join & "scan">'
    assert document.find_all("button", id="theme-toggle")[0].attributes["class"] == "icon-button"
    assert (
        document.find_all("button", id="ai-settings-toggle")[0].attributes["class"]
        == "icon-button"
    )


def test_settings_offer_model_choices_and_link_to_the_setup_guide():
    """Expose model selection without a free-text model name field.

    Keep terminal setup instructions in the guide linked from the report.

    """
    document = ReportDOM(render_html(_profile())).root
    assert document.find_all("select", id="ai-model")
    assert not document.find_all("input", id="ai-model")
    assert document.find_all("button", id="ai-model-refresh")
    assert not document.find_all(id="ai-provider-help")
    assert "codex login" not in document.text()
    assert "--ai-agent codex" not in document.text()
    guide = document.find_all("a", css="ai-help-link")[0]
    assert guide.attributes["href"].endswith("/user_guide/ai/")
    assert guide.attributes["aria-label"] == "Open AI setup guide"
    assert guide.text() == ""
    assert guide.find_all("svg")
    controls = document.find_all("div", css="ai-dialog-controls")[0]
    assert controls.find_all("a")[0] is guide
    close = controls.find_all("button")[0]
    assert close.attributes["aria-label"] == "Close AI settings"
    assert close.text() == ""
    assert close.find_all("svg")
    model_controls = document.find_all("div", css="ai-model-control")[0]
    assert model_controls.find_all("select")[0].attributes["id"] == "ai-model"
    refresh = model_controls.find_all("button")[0]
    assert refresh.attributes["aria-label"] == "Refresh models"
    assert refresh.text() == ""
    assert refresh.find_all("svg")
