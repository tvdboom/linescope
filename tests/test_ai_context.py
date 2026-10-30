"""LineScope.

Author: Mavs
Description: Verify bounded AI prompts preserve source and metric semantics.

"""

from dataclasses import replace
import json

import pytest

from linescope.ai.context import build_context, build_prompt
from linescope.model import (
    BackendCapabilities,
    FunctionStats,
    LineStats,
    MemoryStats,
    ProcessMemoryStats,
    ProfileResult,
    ProfileRun,
    SourceLocation,
    SourceUnit,
    SparkExecution,
    SparkExecutionStats,
    SparkOperator,
    SymbolRef,
)


def _profile() -> ProfileResult:
    """Build separate captured scopes with known and unavailable metrics.

    Returns
    -------
    ProfileResult
        Python snapshots with resolved callers and a sampled child scope.

    """
    source = SourceUnit("main", "main.py", "def work():\n    return helper()\n# unmeasured\n")
    helper = SourceUnit("helper", "helper.py", "def helper():\n    return [1, 2, 3]\n")
    caller = SourceUnit("caller", "caller.py", "result = work()\n")
    other = SourceUnit("other", "other.py", "unrelated = True\n")
    root = ProfileRun(
        id="root",
        elapsed_ns=1_000,
        metadata={"parameters": {"api_key": "secret-in-parameters"}},
        lines=[
            LineStats(
                SourceLocation("main", 2),
                wall_time_ns=800,
                hits=1,
                memory=MemoryStats(delta_bytes=-16),
                ram=ProcessMemoryStats(rss_bytes=4_096, delta_bytes=128, peak_bytes=8_192),
                calls=[SymbolRef("helper", 2, 11, 17, SourceLocation("helper", 1))],
            ),
            LineStats(
                SourceLocation("caller", 1),
                calls=[SymbolRef("work", 1, 9, 13, SourceLocation("main", 1))],
            ),
        ],
        functions=[FunctionStats("main", "work", 1, line_count=2)],
        children=[
            ProfileRun(
                id="child",
                parent_id="root",
                elapsed_ns=500,
                lines=[LineStats(SourceLocation("main", 2), samples=3)],
            )
        ],
    )
    return ProfileResult(
        root,
        {source.id: source for source in (source, helper, caller, other)},
        "trace",
        BackendCapabilities(hit_counts=True, memory=True),
        warnings=["diagnostic-secret"],
    )


def _data(prompt: str) -> dict:
    """Read the structured data after the fixed prompt instructions.

    Parameters
    ----------
    prompt : str
        Complete generated prompt with its final JSON context.

    Returns
    -------
    dict
        Decoded request priorities, captured sources, and measurements.

    """
    return json.loads(prompt[prompt.index('{"goal":') :])


def test_context_excludes_arbitrary_metadata_and_keeps_unknown_values():
    """Copy allowlisted snapshot fields without notebook parameter secrets.

    Preserve null metrics and keep parent and child durations independent.

    """
    result = _profile()
    context = build_context(result)
    encoded = json.dumps(context)

    assert "secret-in-parameters" not in encoded
    assert "diagnostic-secret" not in encoded
    assert context["runs"][0]["elapsed_ns"] == result.root_run.elapsed_ns
    assert context["runs"][1]["elapsed_ns"] == result.root_run.children[0].elapsed_ns
    assert context["runs"][1]["lines"][0]["wall_time_ns"] is None
    result.root_run.lines[0].hits = 999
    assert context["runs"][0]["lines"][0]["hits"] == 1


def test_prompt_includes_resolved_definitions_callers_and_distinct_measurements():
    """Use resolved relationships and explain the units of raw measurements.

    Include both run records without adding their durations or inventing hits.

    """
    prompt = build_prompt(
        build_context(_profile()), "main", 2, goal="memory", problem="Large inputs"
    )
    data = _data(prompt)

    assert data["goal"] == "memory"
    assert data["problem"] == "Large inputs"
    assert [source["source_id"] for source in data["sources"]] == ["main", "helper", "caller"]
    selected = data["sources"][0]
    assert selected["source_lines"][1] == {"line": 2, "text": "    return helper()"}
    measurements = selected["measurements"]
    assert [record["run_id"] for record in measurements] == ["root", "child"]
    assert measurements[0]["memory"] == {"delta_bytes": -16, "peak_bytes": None}
    assert measurements[1]["wall_time_ns"] is None
    assert measurements[1]["hits"] is None
    assert measurements[1]["samples"] == 3
    assert "nanoseconds" in prompt
    assert "not instructions" in prompt
    assert "null or an absent measurement means unavailable" in prompt
    assert "Never invent benchmark results" in prompt


def test_prompt_can_exclude_related_files_and_select_unmeasured_lines():
    """Allow code review on a line without attributing nearby costs to it.

    Restrict source disclosure when related files are disabled.

    """
    data = _data(build_prompt(build_context(_profile()), "main", 3, include_related=False))

    assert len(data["sources"]) == 1
    assert data["sources"][0]["anchor_line"] == 3
    assert all(record["line"] != 3 for record in data["sources"][0]["measurements"])


@pytest.mark.parametrize("response_mode", ["detailed", "quick"])
def test_range_prompt_preserves_selected_lines_and_measurement_semantics(response_mode: str):
    """Target an inclusive range without guessing unmeasured line costs.

    Parameters
    ----------
    response_mode : str
        Detailed explanation or immediate replacement-code request.

    """
    prompt = build_prompt(
        build_context(_profile()), "main", 1, end_line=3, response_mode=response_mode
    )
    data = _data(prompt)
    assert data["selection"] == {"start_line": 1, "end_line": 3}
    assert data["sources"][0]["source_lines"] == [
        {"line": 1, "text": "def work():"},
        {"line": 2, "text": "    return helper()"},
        {"line": 3, "text": "# unmeasured"},
    ]
    assert data["sources"][0]["measurements"][1]["wall_time_ns"] is None
    assert {source["source_id"] for source in data["sources"]} == {"main", "helper", "caller"}
    if response_mode == "quick":
        assert "Return one fenced Python code block" in prompt
        assert "at most four short lines" in prompt
        assert "No performance gain to update." in prompt
        assert "Do not echo unchanged code" in prompt
        assert "Return: (1) diagnosis" not in prompt
        assert data["goal"] == "time"


@pytest.mark.parametrize("response_mode", ["detailed", "quick"])
def test_disjoint_selection_preserves_exact_lines_and_surrounding_context(response_mode: str):
    """Keep gaps as context rather than silently selecting their contents.

    Parameters
    ----------
    response_mode : str
        Detailed review or quick performance suggestion format.

    """
    prompt = build_prompt(
        build_context(_profile()),
        "main",
        1,
        selected_lines=[3, 1],
        response_mode=response_mode,
    )
    data = _data(prompt)
    assert data["selection"] == {"start_line": 1, "end_line": 3, "lines": [1, 3]}
    assert [item["line"] for item in data["sources"][0]["source_lines"]] == [1, 2, 3]
    assert "only those exact line numbers are selected" in prompt
    assert _data(build_prompt(build_context(_profile()), "main", 3, selected_lines=[3]))[
        "selection"
    ] == {"start_line": 3, "end_line": 3, "lines": [3]}


@pytest.mark.parametrize("selected_lines", [[], [0], [4], [True], [1, "3"], "1,3", [2, 3]])
def test_disjoint_selection_rejects_invalid_or_mismatched_lines(selected_lines: object):
    """Validate every requested line against the exact captured snapshot.

    Parameters
    ----------
    selected_lines : object
        Invalid line values or a selection missing its declared first line.

    """
    with pytest.raises(ValueError, match=r"[Ss]elect"):
        build_prompt(build_context(_profile()), "main", 1, selected_lines=selected_lines)


def test_disjoint_selection_rejects_mismatched_end_and_spark_scope():
    """Reject conflicting bounds and line selections attached to Spark.

    Keep request validation independent from the browser's gesture handling.

    """
    context = build_context(_profile())
    with pytest.raises(ValueError, match="range bounds"):
        build_prompt(context, "main", 1, end_line=2, selected_lines=[1, 3])
    with pytest.raises(ValueError, match="source lines"):
        build_prompt(context, scope="spark", selected_lines=[1])


def test_disjoint_selection_retains_distant_lines_before_filling_context():
    """Preserve both selected endpoints when intervening code is too large.

    Keep omission flags honest while retaining complete selected source.

    """
    profile = _profile()
    profile.sources["main"] = replace(profile.sources["main"], source="value = 1\n" * 3000)
    data = _data(
        build_prompt(
            build_context(profile), "main", 1, selected_lines=[1, 3000], response_mode="quick"
        )
    )
    assert data["selection"] == {"start_line": 1, "end_line": 3000, "lines": [1, 3000]}
    source = data["sources"][0]
    assert source["source_omitted"] is True
    assert source["source_lines"][0] == {"line": 1, "text": "value = 1"}
    assert source["source_lines"][-1] == {"line": 3000, "text": "value = 1"}


def test_quick_review_shares_evidence_and_constraints_with_detailed_review():
    """Preserve the full review context while allowing a small surrounding edit.

    Compare the actual prompt payloads so quick mode retains callers,
    definitions, unknown measurements, and user constraints independently of
    its shorter response format.

    """
    profile = _profile()
    profile.root_run.spark_executions = [
        SparkExecution(
            "action",
            location=SourceLocation("main", 2),
            executed_plan="Final AQE plan",
            operators=[SparkOperator("scan", "Scan", metrics={"rows": 200})],
        )
    ]
    context = build_context(profile)
    options = {"goal": "time", "problem": "Preserve order and exception behavior", "end_line": 2}
    detailed = build_prompt(context, "main", 2, **options)
    quick = build_prompt(context, "main", 2, response_mode="quick", **options)

    assert _data(quick) == _data(detailed)
    assert _data(quick)["problem"] == options["problem"]
    assert _data(quick)["spark_executions"][0]["executed_plan"] == "Final AQE plan"
    assert [item["source_id"] for item in _data(quick)["sources"]] == ["main", "helper", "caller"]
    common = detailed[: detailed.index("Return: (1) diagnosis")]
    assert quick.startswith(common)
    assert "not a strict replacement boundary" in quick
    assert "smallest surrounding code block" in quick
    assert "including required imports" in quick
    assert "samples are observations, not hits" in quick
    assert "Spark executor_time_ns is cumulative task work" in quick
    assert "Do not force Spark actions" in quick


def test_quick_review_preserves_related_source_exclusion_and_performance_priority():
    """Honor source disclosure and workload constraints in immediate reviews.

    A quick suggestion prioritizes performance even if a detailed review was
    configured for memory, without re-enabling excluded project files.

    """
    prompt = build_prompt(
        build_context(_profile()),
        "main",
        2,
        goal="memory",
        problem="Keep the public API unchanged",
        include_related=False,
        response_mode="quick",
    )
    data = _data(prompt)
    assert data["goal"] == "time"
    assert data["problem"] == "Keep the public API unchanged"
    assert [item["source_id"] for item in data["sources"]] == ["main"]


@pytest.mark.parametrize("end_line", [0, 1, 4, True, "3", 2.5])
def test_range_prompt_rejects_invalid_or_cross_snapshot_bounds(end_line: object):
    """Reject a range that does not fit the exact captured snapshot.

    Parameters
    ----------
    end_line : object
        Invalid end position for a range beginning on line two.

    """
    with pytest.raises(ValueError, match="valid range"):
        build_prompt(build_context(_profile()), "main", 2, end_line=end_line)


def test_quick_prompt_targets_single_lines_and_refuses_truncated_replacements():
    """Keep code replacements complete and preserve single-line targeting.

    A huge selection must not silently produce replacement code for only
    the retained fragment of a source snapshot.

    """
    profile = _profile()
    prompt = build_prompt(build_context(profile), "main", 2, response_mode="quick")
    assert _data(prompt)["selection"] == {"start_line": 2, "end_line": 2}
    profile.sources["main"] = replace(profile.sources["main"], source="value = 1\n" * 3000)
    with pytest.raises(ValueError, match="Select fewer lines"):
        build_prompt(build_context(profile), "main", 1, end_line=3000)
    with pytest.raises(ValueError, match="source lines"):
        build_prompt(build_context(profile), scope="spark", response_mode="quick")
    with pytest.raises(ValueError, match="not a valid _ResponseMode"):
        build_prompt(build_context(profile), "main", 1, response_mode="unknown")


def test_prompt_does_not_guess_links_to_unknown_or_ambiguous_definitions():
    """Ignore destinations without captured source and leave unlinked files out.

    Existing conservative references are the sole evidence for relationships.

    """
    profile = _profile()
    profile.root_run.lines[0].calls = [
        SymbolRef("external", 2, 11, 19, SourceLocation("missing-source", 1))
    ]
    profile.root_run.lines[1].calls = []
    data = _data(build_prompt(build_context(profile), "main", 2))

    assert [source["source_id"] for source in data["sources"]] == ["main"]


def test_prompt_retains_selected_line_under_context_limits():
    """Bound large source and metric sets while retaining the actual selection.

    Indicate omitted lines and measurements instead of silently losing them.

    """
    profile = _profile()
    lines = [f"value_{index} = '{'x' * 200}'" for index in range(2_000)]
    lines[1_500] = "selected_marker = helper()"
    profile.sources["main"] = replace(profile.sources["main"], source="\n".join(lines))
    profile.root_run.lines = [
        LineStats(SourceLocation("main", index)) for index in range(1, 2_001)
    ]
    prompt = build_prompt(build_context(profile), "main", 1_501)
    selected = _data(prompt)["sources"][0]

    assert len(prompt) <= 80_000
    assert {"line": 1_501, "text": "selected_marker = helper()"} in selected["source_lines"]
    assert selected["source_omitted"]
    assert selected["measurements_omitted"]
    assert selected["measurements"][0]["line"] == 1_501


def test_prompt_bounds_long_single_lines_and_many_related_sources():
    """Clip exceptional lines and identify omitted related sources.

    Keep a syntactically complete JSON context despite omitted source text.

    """
    profile = _profile()
    profile.sources["main"] = replace(profile.sources["main"], source="z" * 100_000)
    profile.root_run.lines[0].location = SourceLocation("main", 1)
    for index in range(20):
        identity = f"related-{index}"
        profile.sources[identity] = SourceUnit(identity, identity + ".py", "x" * 20_000)
        profile.root_run.lines[0].calls.append(
            SymbolRef(identity, 1, index, index + 1, SourceLocation(identity, 1))
        )
    prompt = build_prompt(build_context(profile), "main", 1)
    data = _data(prompt)

    assert len(prompt) <= 80_000
    assert len(data["sources"]) == 6
    assert data["sources"][0]["source_omitted"]
    assert "[omitted]" in data["sources"][0]["source_lines"][0]["text"]
    assert data["related_sources_omitted"]


def test_prompt_keeps_spark_driver_and_executor_work_distinct():
    """Include captured executed plans and normalized Spark metric semantics.

    Exclude arbitrary Spark metadata and unrelated action locations.

    """
    profile = _profile()
    profile.root_run.spark_executions = [
        SparkExecution(
            "action",
            location=SourceLocation("main", 2),
            stats=SparkExecutionStats(wall_time_ns=1_000, executor_time_ns=8_000),
            executed_plan="Final captured plan",
            initial_plan="Obsolete initial plan",
            metadata={"token": "spark-metadata-secret"},
        ),
        SparkExecution("unrelated", location=SourceLocation("other", 1)),
    ]
    prompt = build_prompt(build_context(profile), "main", 2)
    actions = _data(prompt)["spark_executions"]

    assert len(actions) == 1
    assert actions[0]["stats"]["wall_time_ns"] == 1_000
    assert actions[0]["stats"]["executor_time_ns"] == 8_000
    assert actions[0]["executed_plan"] == "Final captured plan"
    assert actions[0]["initial_plan"] == "Obsolete initial plan"
    assert "spark-metadata-secret" not in prompt
    assert "cumulative task work, not driver wall duration" in prompt


def test_spark_context_keeps_complete_plans_operators_stages_and_provenance():
    """Keep captured Spark evidence without the former short plan limit.

    Snapshot mutable counters, preserve unavailable values, and exclude
    arbitrary execution metadata from the preview.

    """
    profile = _profile()
    execution = SparkExecution(
        "action",
        location=SourceLocation("main", 2),
        executed_plan="Final AQE plan\n" * 400,
        initial_plan="Initial physical plan",
        optimized_plan="Optimized logical plan",
        parsed_plan="Parsed logical plan",
        analyzed_plan="Analyzed logical plan",
        operators=[
            SparkOperator(
                "join",
                "BroadcastHashJoin",
                metrics={"time": {"name": "time to build", "type": "timing", "value": 42}},
                children=[SparkOperator("scan", "Scan", metrics={"rows": 200})],
            )
        ],
        stages=[{"id": 3, "tasks": 4, "duration_p95_ns": 1200}],
        jobs=[7],
        metadata={"plan_origin": "query listener", "aqe_final_plan": True, "token": "private"},
    )
    profile.root_run.spark_executions = [execution]
    context = build_context(profile)
    execution.stages[0]["tasks"] = 999
    execution.operators[0].metrics["time"]["value"] = 999
    execution.jobs.append(8)
    action = _data(build_prompt(context, "main", 2))["spark_executions"][0]

    assert action["executed_plan"] == execution.executed_plan
    assert action["initial_plan"] == execution.initial_plan
    assert action["optimized_plan"] == execution.optimized_plan
    assert action["parsed_plan"] == execution.parsed_plan
    assert action["analyzed_plan"] == execution.analyzed_plan
    assert action["operators"][0]["metrics"]["time"]["value"] == 42
    assert action["operators"][0]["children"][0]["metrics"]["rows"] == 200
    assert action["stages"][0]["tasks"] == 4
    assert action["jobs"] == [7]
    assert action["stats"]["peak_memory_bytes"] is None
    assert action["plan_context"] == {"plan_origin": "query listener", "aqe_final_plan": True}
    assert not action["context_omitted"]
    assert "private" not in json.dumps(context)


def test_spark_overview_includes_child_actions_and_actions_without_source():
    """Review all captured actions without requiring a source location.

    Preserve distinct run identities and support a focused action selection
    while keeping source disclosure optional.

    """
    profile = _profile()
    profile.root_run.spark_executions = [SparkExecution("root-action")]
    profile.root_run.children[0].spark_executions = [
        SparkExecution("child-action", location=SourceLocation("main", 2))
    ]
    context = build_context(profile)
    overview = _data(build_prompt(context, scope="spark"))
    assert [(item["id"], item["run_id"]) for item in overview["spark_executions"]] == [
        ("root-action", "root"),
        ("child-action", "child"),
    ]
    assert overview["spark_executions"][0]["source_id"] is None
    assert overview["sources"][0]["reason"] == "Spark action trigger"
    selected = _data(
        build_prompt(context, scope="spark", execution_id="child-action", include_related=False)
    )
    assert selected["sources"] == []
    assert [item["id"] for item in selected["spark_executions"]] == ["child-action"]


def test_spark_line_retains_explicitly_linked_actions_without_location():
    """Use recorded action links without guessing missing source positions.

    The line's own correlation remains useful when the action collector
    could not resolve a trigger location independently.

    """
    profile = _profile()
    profile.root_run.lines[0].spark_executions = ["linked"]
    profile.root_run.spark_executions = [SparkExecution("linked")]
    action = _data(build_prompt(build_context(profile), "main", 2))["spark_executions"][0]
    assert action["id"] == "linked"
    assert action["source_id"] is None


def test_large_spark_evidence_marks_partial_fields_and_stays_within_budget():
    """Bound plans and nested metrics while retaining reviewable JSON.

    JSON escaping counts toward the limit, including in operator descriptions
    and stage details. Omitted evidence must be explicitly labeled.

    """
    profile = _profile()
    profile.root_run.spark_executions = [
        SparkExecution(
            "large",
            executed_plan="\x00" * 100_000,
            operators=[SparkOperator("scan", "Scan", "large" * 20_000)],
            stages=[{"id": index, "detail": "\x00" * 1000} for index in range(100)],
        )
    ]
    prompt = build_prompt(build_context(profile), scope="spark")
    action = _data(prompt)["spark_executions"][0]
    assert len(prompt) <= 80_000
    assert action["id"] == "large"
    assert action["context_omitted"]
    assert "[omitted]" in action["executed_plan"]


@pytest.mark.parametrize(
    "options", [{"scope": "spark"}, {"scope": "spark", "execution_id": "missing"}]
)
def test_spark_selection_rejects_missing_captured_actions(options):
    """Reject selections that have no captured Spark evidence.

    Parameters
    ----------
    options : dict[str, str]
        Overview or action selection supplied by the report UI.

    """
    with pytest.raises(ValueError, match="Spark action"):
        build_prompt(build_context(_profile()), **options)


@pytest.mark.parametrize(
    ("source_id", "line", "options"),
    [
        ("missing", 1, {}),
        ("main", 0, {}),
        ("main", 4, {}),
        ("main", True, {}),
        ("main", 1.5, {}),
        ("main", 1, {"goal": "price"}),
        ("main", 1, {"problem": "x" * 4_001}),
        ("main", 1, {"problem": None}),
        ("main", 1, {"include_related": "yes"}),
    ],
)
def test_prompt_rejects_invalid_selection_and_options(source_id, line, options):
    """Reject invalid UI values before preparing any provider request.

    Parameters
    ----------
    source_id : str
        Supplied captured-source identifier.

    line : int | float | bool
        Supplied one-based line value, including invalid values.

    options : dict
        Optional request fields to validate at the prompt boundary.

    """
    with pytest.raises(ValueError, match=r"Select|valid|Describe|boolean"):
        build_prompt(build_context(_profile()), source_id, line, **options)


def test_context_includes_run_owned_notebook_snapshots():
    """Retain run-owned notebook source absent from the source registry.

    Fail clearly on future payload versions rather than misreading them.

    """
    profile = _profile()
    profile.root_run.children[0].source = SourceUnit("cell", "Cell 1", "value = 1\n")
    profile.root_run.lines[0].notebook_runs = ["child"]
    context = build_context(profile)

    assert "cell" in context["sources"]
    assert _data(build_prompt(context, "cell", 1))["sources"][0]["path"] == "Cell 1"
    related = _data(build_prompt(context, "main", 2))["sources"]
    assert any(source["reason"] == "captured child notebook" for source in related)
    context["version"] = 999
    with pytest.raises(ValueError, match="version"):
        build_prompt(context, "cell", 1)


def test_empty_snapshot_retains_the_rendered_blank_line():
    """Prepare a blank displayed source row without inventing measurements.

    Match the report's one-row representation of an empty captured file.

    """
    profile = _profile()
    profile.sources["empty"] = SourceUnit("empty", "empty.py", "")
    selected = _data(build_prompt(build_context(profile), "empty", 1))["sources"][0]

    assert selected["source_lines"] == [{"line": 1, "text": ""}]
    assert selected["measurements"] == []
    assert not selected["source_omitted"]


def test_prompt_character_limit_includes_json_escaping_and_spark_plans():
    """Count encoded characters when source strings need JSON escaping.

    Retain an omission flag when available Spark plans exceed the final budget.

    """
    profile = _profile()
    profile.sources["main"] = replace(profile.sources["main"], source="\x00" * 20_000)
    profile.root_run.spark_executions = [
        SparkExecution(
            f"action-{index}",
            location=SourceLocation("main", 1),
            executed_plan="\x00" * 10_000,
        )
        for index in range(100)
    ]
    prompt = build_prompt(build_context(profile), "main", 1, problem="\x00" * 4_000)
    data = _data(prompt)

    assert len(prompt) <= 80_000
    assert data["spark_executions_omitted"]
    assert data["sources"][0]["source_lines"][0]["line"] == 1
