from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from vulnclaw.config import settings
from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.config.terminal import TerminalConfig
from vulnclaw.config_service import config_document, update_config
from vulnclaw.management_service import execute_management


def test_terminal_palettes_are_independent_and_round_trip_as_complete_yaml(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(settings, "ensure_dirs", lambda: None)
    config = VulnClawConfig()
    themes = config.terminal.model_dump()["themes"]
    themes["forest"] = {**themes["blue"], "transcript": "#233029"}
    themes["blue"]["action"] = "#aabbcc"
    updated = update_config({"terminal.themes": themes, "terminal.theme": "forest"}, config=config)
    saved = yaml.safe_load(settings.CONFIG_FILE.read_text())
    assert set(saved["terminal"]) == {"theme", "themes"}
    assert len(saved["terminal"]["themes"]["forest"]) == 24
    assert saved["terminal"]["themes"]["forest"]["action"] == "#7aa2f7"
    assert saved["terminal"]["themes"]["orange"]["transcript"] == "#1e2127"
    assert settings.load_config().terminal == updated.terminal
    assert config_document(settings.load_config())["terminal_configured"] is True
    assert config.terminal.themes["blue"].action == "#7aa2f7"


@pytest.mark.parametrize("change", ["missing_color", "bad_color", "bad_name", "unknown_theme"])
def test_terminal_edits_validate_before_saving(monkeypatch, change):
    save = Mock()
    monkeypatch.setattr("vulnclaw.config_service.save_config", save)
    config = VulnClawConfig()
    terminal = config.terminal.model_dump()
    if change == "missing_color":
        del terminal["themes"]["blue"]["text"]
    elif change == "bad_color":
        terminal["themes"]["blue"]["border"] = "blue"
    elif change == "bad_name":
        terminal["themes"]["bad name"] = deepcopy(terminal["themes"]["blue"])
    else:
        terminal["theme"] = "missing"
    with pytest.raises(ValueError):
        update_config({"terminal": terminal}, config=config)
    save.assert_not_called()


def test_theme_configuration_replaces_defaults_and_explicit_main_configuration_wins():
    base = VulnClawConfig()
    assert config_document(settings._merge_config(base, {"session": {"language": "zh"}}))["terminal_configured"] is False
    forest = base.terminal.model_dump()["themes"]["blue"]
    merged = settings._merge_config(base, {"terminal": {"theme": "forest", "themes": {"forest": forest}}})
    assert set(merged.terminal.themes) == {"forest"}
    assert config_document(merged)["terminal_configured"] is True
    assert "shared" not in TerminalConfig.model_json_schema()["properties"]


def test_theme_names_that_match_secret_fields_keep_their_public_colors():
    config = VulnClawConfig()
    config.terminal.themes["token"] = config.terminal.themes["blue"].model_copy(deep=True)
    document = config_document(config)
    assert document["config"]["terminal"]["themes"]["token"]["text"] == "#e1e5ec"
    assert not any(path.startswith("terminal.") for path in document["secret_paths"])


async def test_theme_only_updates_preserve_the_active_agent(monkeypatch):
    monkeypatch.setattr("vulnclaw.config_service.save_config", lambda _config: None)
    application = SimpleNamespace(config=VulnClawConfig(), apply_config=Mock())
    result = await execute_management(application, "config.update", {"changes": {"terminal.theme": "orange"}})
    application.apply_config.assert_not_called()
    assert application.config.terminal.theme == "orange"
    assert result["config"]["terminal"]["theme"] == "orange"
    assert config_document(application.config)["terminal_configured"] is True


@pytest.mark.parametrize("terminal", [None, {"theme": "missing", "themes": {}}])
def test_invalid_explicit_main_configuration_is_not_overwritten_by_legacy_migration(terminal):
    merged = settings._merge_config(VulnClawConfig(), {"terminal": terminal})
    assert config_document(merged)["terminal_configured"] is True
