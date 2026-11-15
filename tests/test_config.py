import pytest

from core.config import ConfigError, config_from_dict


def test_defaults_and_sections_normalised():
    cfg = config_from_dict(
        {"sections": {"weather": True, "news": {"max_items": 3}, "tasks": False}}
    )
    assert cfg.timezone == "Asia/Kolkata" and cfg.send_time == "07:00"
    assert cfg.section_enabled("weather") and cfg.section_enabled("news")
    assert not cfg.section_enabled("tasks") and not cfg.section_enabled("nope")
    assert cfg.section_options("news")["max_items"] == 3


@pytest.mark.parametrize(
    "raw",
    [
        {"timezone": "Mars/Base"},
        {"send_time": 420},  # unquoted 07:00 parses as an int in YAML
        {"send_time": "7am"},
        {"feeds": [{"name": "x"}]},
        {"feeds": [{"name": "x", "url": "ftp://x"}]},
        {"topics": [{"keywords": ["a"]}]},
    ],
)
def test_invalid_config_raises(raw):
    with pytest.raises(ConfigError):
        config_from_dict(raw)


def test_string_topic_gets_default_keyword():
    cfg = config_from_dict({"topics": ["Cricket"]})
    assert cfg.topics[0].keywords == ["cricket"]


def test_shipped_config_is_valid():
    from core.config import load_config

    cfg = load_config("config.yaml")
    assert cfg.send_time == "07:00" and cfg.location.city == "Hyderabad"
