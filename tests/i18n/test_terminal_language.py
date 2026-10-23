from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from vulnclaw.config.schema import VulnClawConfig
from vulnclaw.config_service import config_document, update_config
from vulnclaw.i18n import current_lang, init_i18n
from vulnclaw.management_service import execute_management


@pytest.fixture(autouse=True)
def restore_translator():
    previous = current_lang()
    yield
    init_i18n(lang=previous)


@pytest.mark.parametrize(("mode", "override", "system", "expected"), [
    ("zh", "en", "en_US.UTF-8", "zh"),
    ("en", "zh", "zh_TW.UTF-8", "en"),
    ("auto", "en", "zh_CN.UTF-8", "en"),
    ("auto", "zh", "en_US.UTF-8", "zh"),
    ("auto", "", "zh_TW.UTF-8", "zh"),
    ("auto", "", "C.UTF-8", "en"),
])
def test_configuration_preserves_the_language_choice_and_backend_detection_is_local(
    monkeypatch, mode, override, system, expected,
):
    monkeypatch.setenv("VULNCLAW_LANG", override)
    monkeypatch.setenv("LANG", system)
    config = VulnClawConfig()
    config.session.language = mode
    previous = current_lang()
    assert config_document(config)["config"]["session"]["language"] == mode
    assert current_lang() == previous
    assert init_i18n(config=config).lang == expected


@pytest.mark.parametrize(("operation", "arguments"), [
    ("language.set", {"language": "zh"}),
    ("config.update", {"changes": {"session.language": "zh"}}),
])
async def test_language_changes_save_and_synchronize_the_backend_and_terminal(
    monkeypatch, operation, arguments,
):
    init_i18n(lang="en")
    saved = Mock()
    monkeypatch.setattr("vulnclaw.config_service.save_config", saved)
    application = SimpleNamespace(config=VulnClawConfig())
    application.apply_config = AsyncMock(
        side_effect=lambda config: setattr(application, "config", config),
    )
    result = await execute_management(application, operation, arguments)
    saved.assert_called_once()
    assert application.config.session.language == "zh"
    assert current_lang() == "zh"
    if operation == "language.set":
        assert result["language"] == "zh"
        assert "中文" in result["message"]
    else:
        assert result["config"]["session"]["language"] == "zh"


async def test_auto_language_switch_and_failed_saves_keep_the_live_locale_consistent(monkeypatch):
    init_i18n(lang="zh")
    monkeypatch.setenv("VULNCLAW_LANG", "en")
    save = Mock(side_effect=OSError("synthetic write failure"))
    monkeypatch.setattr("vulnclaw.config_service.save_config", save)
    application = SimpleNamespace(config=VulnClawConfig())
    application.config.session.language = "zh"
    application.apply_config = AsyncMock(
        side_effect=lambda config: setattr(application, "config", config),
    )
    with pytest.raises(OSError, match="synthetic write failure"):
        await execute_management(application, "language.set", {"language": "auto"})
    assert application.config.session.language == current_lang() == "zh"
    save.side_effect = None
    result = await execute_management(application, "language.set", {"language": "auto"})
    assert result["language"] == "auto"
    assert current_lang() == "en"
    assert application.config.session.language == "auto"


def test_unsupported_settings_language_is_rejected_before_writing(monkeypatch):
    save = Mock()
    monkeypatch.setattr("vulnclaw.config_service.save_config", save)
    with pytest.raises(ValueError):
        update_config({"session.language": "invalid"}, config=VulnClawConfig())
    save.assert_not_called()


async def test_runtime_initializes_translations_from_the_saved_configuration(monkeypatch):
    from vulnclaw.application import create_runtime

    init_i18n(lang="en")
    config = VulnClawConfig()
    config.session.language = "zh"
    manager = Mock()
    manager.start_enabled_servers.return_value = 0
    monkeypatch.setattr("vulnclaw.mcp.lifecycle.MCPLifecycleManager", lambda _config: manager)
    monkeypatch.setattr("vulnclaw.agent.core.AgentCore", lambda *_args: object())
    runtime = await create_runtime(config)
    assert runtime.config is config
    assert current_lang() == "zh"
