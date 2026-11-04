from __future__ import annotations

import os
import plistlib
import stat
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import install_macos_inference as installer

KEY = "synthetic-inference-key-" + "x" * 32


@pytest.mark.parametrize("tool_parser", [None, "qwen3_coder_xml", "custom-parser"])
def test_run_keeps_credentials_private_and_telemetry_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool_parser: str | None,
) -> None:
    root = tmp_path / ".config" / "tennis-inference"
    root.mkdir(parents=True)
    (root / "api-key").write_text(KEY + "\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for name, value in {
        "RAPID_MLX_API_KEY": "unrelated-key",
        "RAPID_MLX_TELEMETRY": "1",
        "DO_NOT_TRACK": "0",
        "HF_HUB_DISABLE_TELEMETRY": "0",
        "HF_HUB_OFFLINE": "0",
    }.items():
        monkeypatch.setenv(name, value)
    argv = ["installer", "run", "--executable", "/runtime/rapid-mlx", "--model", "/models/local"]
    if tool_parser:
        argv.extend(["--tool-call-parser", tool_parser])
    monkeypatch.setattr(installer.sys, "argv", argv)
    intercepted = OSError("exec intercepted by test")
    execute = Mock(side_effect=intercepted)
    monkeypatch.setattr(installer.os, "execve", execute)

    with pytest.raises(OSError) as error:
        installer.main()
    assert error.value is intercepted
    executable, command, environment = execute.call_args.args
    assert executable == "/runtime/rapid-mlx"
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8123"
    assert environment["RAPID_MLX_API_KEY"] == KEY
    assert environment["RAPID_MLX_TELEMETRY"] == "0"
    assert environment["DO_NOT_TRACK"] == "1"
    assert environment["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert os.environ["RAPID_MLX_TELEMETRY"] == "1"
    assert KEY not in repr(command) and KEY not in capsys.readouterr().out
    if tool_parser:
        assert command[command.index("--tool-call-parser") + 1] == tool_parser
        assert "--enable-auto-tool-choice" in command
    else:
        assert "--tool-call-parser" not in command
        assert "--enable-auto-tool-choice" not in command


@pytest.mark.parametrize("tool_parser", [None, "qwen3_coder_xml"])
def test_install_preserves_key_and_parser_without_routing_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool_parser: str | None,
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer.os, "getuid", lambda: 501)
    executable = tmp_path / "rapid-mlx"
    executable.touch()
    model = tmp_path / "local-model"
    model.mkdir()
    root = tmp_path / ".config" / "tennis-inference"
    root.mkdir(parents=True)
    key_path = root / "api-key"
    key_path.write_text(KEY + "\n")
    key_path.chmod(0o644)
    argv = [
        "installer",
        "install",
        "--executable",
        str(executable),
        "--model",
        str(model),
        "--port",
        "8124",
    ]
    if tool_parser:
        argv.extend(["--tool-call-parser", tool_parser])
    monkeypatch.setattr(installer.sys, "argv", argv)
    launch = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(installer.subprocess, "run", launch)

    installer.main()
    plist = tmp_path / "Library" / "LaunchAgents" / f"{installer.LABEL}.plist"
    spec = plistlib.loads(plist.read_bytes())
    arguments = spec["ProgramArguments"]
    assert arguments[arguments.index("--port") + 1] == "8124"
    if tool_parser:
        assert arguments[arguments.index("--tool-call-parser") + 1] == tool_parser
    else:
        assert "--tool-call-parser" not in arguments
    assert key_path.read_text() == KEY + "\n"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(plist.stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert KEY.encode() not in plist.read_bytes()
    assert KEY not in (root / "service.py").read_text()
    assert KEY not in capsys.readouterr().out
    assert [call.args[0] for call in launch.call_args_list] == [
        ["launchctl", "bootout", f"gui/501/{installer.LABEL}"],
        ["launchctl", "bootstrap", "gui/501", str(plist)],
    ]
