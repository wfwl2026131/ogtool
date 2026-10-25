

def test_the_listing_records_one_row_per_dialog(tmp_path, monkeypatch):
    """The listing is a result, not a side effect: rows back the counts."""
    import json

    from ogtool.config import Settings
    from ogtool import cli

    config = tmp_path / "config.yaml"
    config.write_text("logs: logs\n", encoding="utf-8")
    settings = Settings(config, log_dir=tmp_path / "logs")
    dialogs = [
        ("t1", "group", "@g", "Group\t12:00\t7"),
        ("t1", "user", "@u", "User\t11:00\t0"),
    ]
    monkeypatch.setattr(cli, "_Pool",
                        lambda *_a, **_k: type("P", (), {
                            "dialogs": lambda self, *a, **k: dialogs})())
    argv = ["--config", str(config), "accounts", "dialogs", "--live"]
    assert cli.run(argv) == 0

    receipt = json.loads(
        sorted((tmp_path / "logs" / "receipts").glob("accounts-*.json"))[-1]
        .read_text())
    assert receipt["planned"] == 2
    assert receipt["ok"] == 2 and receipt["failed"] == 0
    assert receipt["results"]["total"] == 2
    assert receipt["accounts"] == ["t1"]
