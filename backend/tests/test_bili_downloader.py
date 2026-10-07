from pathlib import Path
import subprocess

from services import bili_downloader


def test_rejects_non_bilibili_url_without_running_downloader(tmp_path, monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("downloader must not run for an invalid URL")

    monkeypatch.setattr(bili_downloader.subprocess, "run", fake_run)

    result = bili_downloader.download_bilibili_audio(
        urls=["https://example.com/video/BV123"],
        metadata=[],
        music_dir=str(tmp_path),
    )

    assert called is False
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "invalid_bilibili_url"
    assert result["errors"][0]["retryable"] is False


def test_classifies_412_as_non_retryable_and_does_not_repeat(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ERROR: HTTP Error 412: Precondition Failed; request was banned",
        )

    monkeypatch.setattr(bili_downloader.subprocess, "run", fake_run)

    result = bili_downloader.download_bilibili_audio(
        urls=["https://www.bilibili.com/video/BV123"],
        metadata=[],
        music_dir=str(tmp_path),
        network_mode="direct",
    )

    assert len(commands) == 1
    assert result["status"] == "failed"
    assert result["cookies_configured"] is False
    assert result["errors"][0]["code"] == "bilibili_request_blocked"
    assert result["errors"][0]["retryable"] is False
    assert "VPN" in result["errors"][0]["message"]
    assert "不表示视频本身不可访问" in result["errors"][0]["message"]
    assert "secrets/bilibili-cookies.txt" in result["errors"][0]["message"]


def test_uses_cookie_impersonation_and_single_fragment(tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        output_template = Path(command[command.index("--output") + 1])
        Path(str(output_template).replace("%(ext)s", "mp3")).write_bytes(b"audio")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(bili_downloader.subprocess, "run", fake_run)

    result = bili_downloader.download_bilibili_audio(
        urls=["https://www.bilibili.com/video/BV123"],
        metadata=[{"bvid": "BV123", "artist": "歌手", "title": "歌曲"}],
        music_dir=str(tmp_path),
        cookie_file=str(cookie_file),
    )

    command = commands[0]
    assert command[command.index("--cookies") + 1] == str(cookie_file)
    assert command[command.index("--impersonate") + 1] == "chrome"
    assert command[command.index("--concurrent-fragments") + 1] == "1"
    assert result["status"] == "success"
    assert result["cookies_configured"] is True
    assert result["files"][0]["renamed"] == "歌手-歌曲-BV123.mp3"
    assert result["files"][0]["existing"] is False


def test_existing_bvid_file_is_idempotent(tmp_path, monkeypatch):
    target_dir = tmp_path / "20200101"
    target_dir.mkdir()
    existing = target_dir / "已有歌手-已有歌曲-BV123.mp3"
    existing.write_bytes(b"audio")

    monkeypatch.setattr(
        bili_downloader.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("existing BVID must not be downloaded again")
        ),
    )

    result = bili_downloader.download_bilibili_audio(
        urls=["https://www.bilibili.com/video/BV123"],
        metadata=[{"bvid": "BV123", "artist": "歌手", "title": "歌曲"}],
        music_dir=str(tmp_path),
    )

    assert result["status"] == "success"
    assert result["files"][0]["existing"] is True
    assert result["files"][0]["local_file_path"] == str(existing.resolve())


def test_auto_download_switches_from_blocked_direct_to_proxy(tmp_path, monkeypatch):
    commands = []
    def fake_run(command, **kwargs):
        commands.append(command)
        proxy = command[command.index("--proxy") + 1]
        if not proxy:
            return subprocess.CompletedProcess(command, 1, "", "HTTP Error 412")
        output = Path(command[command.index("--output") + 1])
        Path(str(output).replace("%(ext)s", "mp3")).write_bytes(b"audio")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(bili_downloader.subprocess, "run", fake_run)
    result = bili_downloader.download_bilibili_audio(urls=["https://www.bilibili.com/video/BV123"],
        metadata=[], music_dir=str(tmp_path), network_mode="auto", proxy_url="http://proxy.test:7890")
    assert [c[c.index("--proxy") + 1] for c in commands] == ["", "http://proxy.test:7890"]
    assert result["status"] == "success"
    assert result["files"][0]["network_route"] == "proxy"


def test_direct_download_explicitly_bypasses_environment_proxy(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://unreachable.test:7890")
    commands = []
    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, "", "HTTP Error 412")
    monkeypatch.setattr(bili_downloader.subprocess, "run", fake_run)
    bili_downloader.download_bilibili_audio(urls=["https://www.bilibili.com/video/BV123"],
        metadata=[], music_dir=str(tmp_path), network_mode="direct")
    assert len(commands) == 1
    assert commands[0][commands[0].index("--proxy") + 1] == ""
