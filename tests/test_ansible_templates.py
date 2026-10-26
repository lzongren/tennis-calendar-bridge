from __future__ import annotations

import json
import tomllib
from pathlib import Path

import jinja2
import yaml

from tennis_overview.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def test_private_inference_environment_is_isolated_and_optional():
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT / "ansible/templates"), undefined=jinja2.StrictUndefined
    )
    rendered = env.get_template("inference.env.j2").render(
        tennis_discovery_enabled=True,
        tennis_discovery_api_url="https://model.example.org/v1",
        vault_tennis_discovery_api_key="synthetic-secret",
    )
    assert "TENNIS_DISCOVERY_ENABLED=true" in rendered
    assert "TENNIS_DISCOVERY_API_KEY='synthetic-secret'" in rendered
    assert "TENNIS_ADMIN_TOKEN" not in rendered
    play = yaml.safe_load((ROOT / "ansible/playbook.yml").read_text())[0]
    task = next(
        t for t in play["tasks"] if t["name"] == "Render optional private inference settings"
    )
    assert task["no_log"] and task["ansible.builtin.template"]["mode"] == "0600"
    for name in (".gitignore", ".dockerignore", "scripts/deploy_vps.sh", "ansible/playbook.yml"):
        assert ".inference.env" in (ROOT / name).read_text()


def test_vault_feed_is_rendered_only_into_environment(tmp_path):
    values = yaml.safe_load((ROOT / "ansible/group_vars/tennis_servers/vars.yml").read_text())
    values.update(
        vault_tennis_admin_token="test-admin",
        vault_tennis_calendar_token="test-export",
        vault_tennis_calendar_feeds={
            "EXAMPLE_FEED_URL": "https://example.invalid/private-feed?x=$value"
        },
        vault_tennis_credentials={
            "OTHER_USERNAME": "test-member",
            "OTHER_PASSWORD": "test-password",
        },
        tennis_clubs=[
            dict(
                id="feed-club",
                name="Feed Club",
                provider="courtreserve",
                base_url="https://example.invalid/club",
                options={"calendar_feed_env": "EXAMPLE_FEED_URL"},
            ),
            dict(
                id="browser-club",
                name="Browser Club",
                provider="clubautomation",
                base_url="https://example.invalid/",
                username_env="OTHER_USERNAME",
                password_env="OTHER_PASSWORD",
            ),
        ],
    )
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(ROOT / "ansible/templates"),
        undefined=jinja2.StrictUndefined,
    )
    environment.filters["to_json"] = json.dumps
    environment.globals["lookup"] = lambda kind, key, default="": values.get(key, default)
    dotenv = environment.get_template("env.j2").render(**values)
    clubs = environment.get_template("clubs.toml.j2").render(**values)

    assert "EXAMPLE_FEED_URL='https://example.invalid/private-feed?x=$value'" in dotenv
    assert "OTHER_USERNAME='test-member'" in dotenv
    assert "OTHER_PASSWORD='test-password'" in dotenv
    assert "private-feed" not in clubs
    assert "test-password" not in clubs
    assert tomllib.loads(clubs)["clubs"][0]["options"]["calendar_feed_env"] == "EXAMPLE_FEED_URL"
    config_path = tmp_path / "clubs.toml"
    config_path.write_text(clubs)
    config = load_config(str(config_path))
    assert config.clubs[0].username_env == config.clubs[0].password_env == ""
    assert config.clubs[1].username_env == "OTHER_USERNAME"


def test_feed_secret_validation_and_rendering_are_not_logged():
    play = yaml.safe_load((ROOT / "ansible/playbook.yml").read_text())[0]
    validate = next(
        task
        for task in play["pre_tasks"]
        if task["name"].startswith("Require vault-backed calendar")
    )
    render = next(
        task
        for task in play["tasks"]
        if task["name"] == "Render runtime environment from vault values"
    )
    assert validate["no_log"] is True
    assert render["no_log"] is True
    assert render["ansible.builtin.template"]["mode"] == "0600"
    assertions = validate["ansible.builtin.assert"]["that"]
    assert any("vault_tennis_calendar_feeds" in expression for expression in assertions)
    sync = next(
        task for task in play["tasks"] if task["name"] == "Sync application source to server"
    )
    assert "--exclude=.vault-pass" in sync["ansible.builtin.command"]["argv"]
