import pytest

from tests.reporting_fixtures import renderer_assessment  # noqa: F401


@pytest.fixture(autouse=True)
def use_assessed_renderer_snapshot(renderer_assessment):  # noqa: F811
    """Output-format tests start after the independently tested assessment seam."""
