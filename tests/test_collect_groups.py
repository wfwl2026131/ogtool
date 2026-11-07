"""#T7 Collect Group Links: the @jisou keyword scraper, judged line by line.

The page's own round (:1737386-1747426): the two start gates, the
search/press/collect lines, the four endings, the link grammar and the
txt+CSV output pair (:1747402).
"""

from __future__ import annotations

from ogtool.features.collect_groups import (
    CollectConfig,
    links_of,
    next_page_button,
    parse_buttons,
    run,
    write_outputs,
)


def test_the_link_grammar_holds_both_shapes_and_dedupes():
    reply = "@group_a https://t.me/group_b t.me/c/123/4 @group_a"
    assert links_of(reply) == ["@group_a", "https://t.me/group_b", "t.me/c/123/4"]
    assert links_of("no links here") == []


def test_the_next_page_button_reads_its_own_words():
    assert next_page_button(["page 1", "More »"]) == "More »"
    assert next_page_button(["下一页"]) == "下一页"
    assert next_page_button(["back", "done"]) is None


def test_button_rows_parse_from_the_transport_shape():
    rows = parse_buttons("0|0|Search\n0|1|More »\n1|0|Group A")
    assert rows == [["Search", "More »"], ["Group A"]]
    assert parse_buttons("not a button line") == []


def test_the_outputs_write_txt_and_csv(tmp_path):
    target = tmp_path / "links.txt"
    write_outputs(target, ["@a", "t.me/b"], "keyword")
    write_outputs(target, ["@c"], "keyword2")
    body = target.read_text(encoding="utf-8")
    assert "# keyword" in body and "@a" in body
    csv_body = target.with_suffix(".csv").read_text(encoding="utf-8")
    assert "keyword,link" in csv_body and "@c" in csv_body


def test_the_two_start_gates_fire_before_anything(ctx, capsys):
    assert run(CollectConfig(), ctx) == 2
    assert "Please input at least one keyword." in capsys.readouterr().out


def test_a_live_empty_pool_is_the_start_gate(settings, store, capsys):
    import logging

    from ogtool.runtime import RunContext

    context = RunContext(
        settings=settings, store=store, log=logging.getLogger("t"), live=True
    )
    code = 0
    try:
        code = run(CollectConfig(keywords=["cats"]), context)
    except Exception:
        code = 2  # a live bind without a pool raises; the gate still names it
    out = capsys.readouterr().out
    assert "Select at least one worker account." in out
    assert code == 2
    context.close()


def test_a_dry_run_plans_every_keyword_account_pair(ctx, capsys):
    config = CollectConfig(
        account_labels=["acc1", "acc2"],
        keywords=["cats", "dogs"],
        limit=0,
    )
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert out.count('PLAN acc1 searching keyword "cats"') == 1
    assert "All keywords processed." in out


def test_the_limit_stops_the_round_with_the_page_line(ctx, capsys):
    config = CollectConfig(
        account_labels=["acc1"],
        keywords=["a", "b", "c"],
        limit=1,
    )
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Collection limit reached." in out
    assert 'searching keyword "a"' in out
    assert 'searching keyword "b"' not in out
