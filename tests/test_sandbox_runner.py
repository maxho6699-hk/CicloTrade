from types import SimpleNamespace

from sandbox_runner.runner_service import execute_in_container


def test_runner_uses_ephemeral_networkless_restricted_container(monkeypatch):
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(
            stdout='{"status":"completed","strategy_classes":["S"]}',
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("sandbox_runner.runner_service.subprocess.run", run)

    result = execute_in_container("import backtrader as bt\nclass S(bt.Strategy):\n    pass\n")

    command = commands[0]
    assert result["status"] == "completed"
    assert command[:2] == ["docker", "run"]
    for required in ("--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--pids-limit", "64"):
        assert required in command
    assert all("shell" not in str(value).lower() for value in command)
