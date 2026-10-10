import subprocess

from fusion_video_pipeline import cli


def test_utf8_relaunch_command_only_wraps_non_utf8_windows_python():
    assert cli._utf8_relaunch_command(
        "nt", 0, "python.exe", ["serve", "--no-browser"]
    ) == [
        "python.exe",
        "-X",
        "utf8",
        "-m",
        "fusion_video_pipeline.cli",
        "serve",
        "--no-browser",
    ]
    assert cli._utf8_relaunch_command("nt", 1, "python.exe", ["serve"]) is None
    assert cli._utf8_relaunch_command("posix", 0, "python", ["serve"]) is None


def test_utf8_relaunch_sets_child_encoding_environment(monkeypatch):
    monkeypatch.setattr(
        cli,
        "_utf8_relaunch_command",
        lambda *args: ["python.exe", "-X", "utf8", "-m", "fusion_video_pipeline.cli"],
    )
    captured = {}

    def fake_run(command, *, env, check):
        captured.update(command=command, env=env, check=check)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli._relaunch_in_utf8_mode_if_needed() == 7
    assert captured["env"]["PYTHONUTF8"] == "1"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["check"] is False


def test_run_command_exposes_all_report_engines():
    parser = cli.build_parser()

    efficient = parser.parse_args(["run", "subtitle.txt", "--report-engine", "efficient"])
    editorial = parser.parse_args(["run", "subtitle.txt", "--report-engine", "editorial"])
    legacy = parser.parse_args(["run", "subtitle.txt"])

    assert efficient.report_engine == "efficient"
    assert editorial.report_engine == "editorial"
    assert legacy.report_engine == "legacy"
