"""#T4 the Client List: CRM rows and the page's own sentences.

The reference's ``client`` table (:1741688-1741707) is a phone-keyed CRM:
CSV import/export, the three delete forms, the counters and the
conversation opener.  Every verdict below is the page's own line.
"""

from __future__ import annotations

from ogtool.features.client_list import (
    ClientListConfig,
    decrypt_file,
    encrypt_file,
    error_word,
    import_csv,
    read_clients_csv,
    run,
)


def _config(**overrides) -> ClientListConfig:
    values: dict = {"task_name": "t"}
    values.update(overrides)
    return ClientListConfig(**values)


def test_csv_rows_are_read_as_the_page_columns(tmp_path):
    path = tmp_path / "clients.csv"
    path.write_text(
        "Phone,Username\n+8613800000001,@alice\n+8613800000002,bob\n",
        encoding="utf-8",
    )
    rows = read_clients_csv(path)
    assert rows[0]["phone"] == "+8613800000001"
    assert rows[1]["username"] == "bob"


def test_import_fills_the_table_and_names_the_verdict(store, tmp_path, capsys):
    path = tmp_path / "clients.csv"
    path.write_text("Phone,Username\n+8613800000001,@alice\n", encoding="utf-8")
    assert import_csv(store, path) == 1
    assert "Import successful: 1" in capsys.readouterr().out
    assert len(store.client_rows()) == 1


def test_an_unreadable_file_fails_the_import(store, tmp_path, capsys):
    assert import_csv(store, tmp_path / "missing.csv") == 0
    out = capsys.readouterr().out
    assert "Import failed" in out


def test_delete_uses_both_forms(store):
    store.upsert_client("+8613800000001", "alice")
    store.upsert_client("+8613800000002", "bob")
    assert store.delete_clients(usernames=["alice"]) == 1
    assert store.delete_clients(phones=["+8613800000002"]) == 1
    assert store.delete_clients() == 0


def test_counters_move_separately(store):
    store.upsert_client("+8613800000001", "alice")
    assert store.bump_client_counter("+8613800000001", success=True) == 1
    assert store.bump_client_counter("+8613800000001", success=True) == 2
    assert store.bump_client_counter("+8613800000001", success=False) == 1


def test_the_error_map_speaks_the_page_sentences():
    assert error_word("PHONE_NUMBER_INVALID") == "Invalid phone number"
    assert (
        error_word("FLOOD_WAIT 5") == "Rate limit exceeded, please wait and try again"
    )
    assert error_word("SOMETHING_ELSE") == "Unknown error occurred"


def test_the_dm_wrapper_round_trips(tmp_path):
    source = tmp_path / "clients.csv"
    source.write_text("Phone,Username\n+8613,@a\n", encoding="utf-8")
    wrapped = encrypt_file(source)
    assert wrapped and wrapped.endswith(".dm")
    back = decrypt_file(wrapped)
    assert back and back.endswith(".csv")
    assert "+8613" in (tmp_path / "clients.csv").read_text(encoding="utf-8")


def test_a_missing_file_fails_the_wrapper(tmp_path, capsys):
    assert encrypt_file(tmp_path / "missing.csv") is None
    assert decrypt_file(tmp_path / "missing.dm") is None
    out = capsys.readouterr().out
    assert "Failed to encrypt file" in out
    assert "Failed to decrypt file" in out


def test_a_dry_run_lists_rows_and_names_the_counters(store, ctx, capsys):
    store.upsert_client("+8613800000001", "alice")
    run(_config(search="alice"), ctx)
    out = capsys.readouterr().out
    assert "+8613800000001 alice" in out
    assert "client: 1 row(s)" in out


def test_the_counter_action_prints_the_page_lines(store, ctx, capsys):
    store.upsert_client("+8613800000001", "alice")
    run(_config(client="+8613800000001", count_success=True), ctx)
    out = capsys.readouterr().out
    assert (
        "ClientListPage: Clicked success count area - Client: "
        "+8613800000001, Add Count: 1 sucList"
    ) in out
    run(_config(client="+8613800000001", count_success=False), ctx)
    out = capsys.readouterr().out
    assert (
        "ClientListPage: Clicked failed count area - Client: "
        "+8613800000001, Failed Count: 1"
    ) in out


def test_the_worker_selector_without_a_worker_names_the_refusal(store, ctx, capsys):
    run(_config(client="+8613999", show_worker=True), ctx)
    out = capsys.readouterr().out
    assert "No worker account IDs found for client" in out


def test_an_unknown_client_file_is_a_delete_failure(store, ctx, capsys):
    run(_config(delete=["+8613999"]), ctx)
    out = capsys.readouterr().out
    assert "Delete failed" in out
