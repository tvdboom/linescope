"""LineScope.

Author: Mavs
Description: Keep the report overview free of the profiling scope banner.

"""

import pytest

from linescope.enums import Backend
from linescope.model import BackendCapabilities, ProfileResult, ProfileRun, SparkExecution
from linescope.render import render_html
from tests.test_render import ReportDOM


@pytest.mark.parametrize("backend", [*Backend, "custom"])
@pytest.mark.parametrize(
    ("run", "capabilities"),
    [
        pytest.param(ProfileRun(), BackendCapabilities(), id="python"),
        pytest.param(
            ProfileRun(spark_executions=[SparkExecution("action")]),
            BackendCapabilities(sampled=True),
            id="spark",
        ),
        pytest.param(
            ProfileRun(children=[ProfileRun(metadata={"child_backend": "trace"})]),
            BackendCapabilities(sampled=True),
            id="child-run",
        ),
        pytest.param(ProfileRun(), BackendCapabilities(sampled=True, gpu=True), id="gpu"),
    ],
)
def test_report_omits_profiling_scope_banner(backend, run, capabilities):
    """Verify report omits profiling scope banner.

    Inspect report scope metadata against the selected collector capabilities
    and captured run context.

    """
    result = ProfileResult(run, {}, backend, capabilities)
    document = ReportDOM(render_html(result)).root
    overview = document.find_all("section", id="overview")[0]
    assert document.find_all(css="profile-scope") == []
    assert "Profiling scope:" not in overview.text()
    assert "Elapsed wall time" in overview.text()
    assert "Most expensive lines" in overview.text()
