

def test_the_listing_records_one_row_per_dialog(tmp_path, monkeypatch):
    """The listing is a result, not a side effect: rows back the counts."""
    import json

    from ogtool import cli

    config = tmp_path / "config.yaml"
    config.write_text("logs: logs\n", encoding="utf-8")
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


def test_the_probe_records_one_row_per_account(tmp_path, monkeypatch):
    """'total: 1 ok: 1' on screen must be backed by a row, in live and dry."""
    import json

    from ogtool import cli

    config = tmp_path / "config.yaml"
    config.write_text("logs: logs\n", encoding="utf-8")
    rows = [("t1", True, "user_id=1 username=@x phone=1****"), ("a", False, "x")]
    monkeypatch.setattr(cli, "_Pool",
                        lambda *_a, **_k: type("P", (), {
                            "test": lambda self, *a, **k: rows})())
    assert cli.run(["--config", str(config), "accounts", "test"]) == 1

    receipt = json.loads(
        sorted((tmp_path / "logs" / "receipts").glob("accounts-*.json"))[-1]
        .read_text())
    assert receipt["planned"] == 2
    assert receipt["ok"] == 1 and receipt["failed"] == 1
    assert receipt["results"]["total"] == 2
    assert receipt["accounts"] == ["t1", "a"]
