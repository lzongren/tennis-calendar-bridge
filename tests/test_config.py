from __future__ import annotations

from pathlib import Path

from tennis_overview.config import load_config


def test_load_config_normalizes_base_path_from_env(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "clubs.toml"
    config_path.write_text(
        """
[app]
calendar_name = "Tennis Calendar"

[[clubs]]
id = "example"
name = "Example Club"
provider = "clubautomation"
base_url = "https://club.example.com/"
username_env = "EXAMPLE_USERNAME"
password_env = "EXAMPLE_PASSWORD"
enabled = false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("TENNIS_BASE_PATH", "tennis/")

    config = load_config(str(config_path))

    assert config.app.base_path == "/tennis"
