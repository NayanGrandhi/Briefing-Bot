from main import EXIT_UNALERTED, main


def test_demo_dry_run_end_to_end(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(root / "data", tmp_path / "data", dirs_exist_ok=True)
    assert main(["--demo", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for needle in (
        "Weather · Hyderabad",
        "Today's schedule",
        "Top news",
        "Tasks",
        "Markets",
        "Tip of the day",
    ):
        assert needle in out
    assert "👍" in out and "Conflict" in out and "Overdue" in out


def test_dry_run_does_not_touch_real_state_db(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    monkeypatch.chdir(tmp_path)
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    main(["--demo", "--dry-run"])
    assert not (tmp_path / "data" / "state.db").exists()


def test_real_run_without_credentials_fails_cleanly(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    assert main(["run"]) == EXIT_UNALERTED  # no Telegram -> cannot alert, exit non-zero


def test_bad_config_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("send_time: 7am\n")
    assert main(["--dry-run"]) == EXIT_UNALERTED


def test_config_flag_works_with_and_without_subcommand(tmp_path):
    from main import parse_args

    assert parse_args(["--config", "x.yaml", "--dry-run"]).config == "x.yaml"
    assert parse_args(["run", "--config", "y.yaml"]).config == "y.yaml"
    assert parse_args(["poll", "--config", "z.yaml"]).config == "z.yaml"
