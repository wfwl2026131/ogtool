"""StaticBucketInviteStrategy: one account bucket per target.

The page's own grammar (:1743019-1743020 "Range expression (e.g.
1-5,7,8)"), its two confirmation lines (:1743021, :1743025), the ignored
line (:1743000) and the 1:1 target-bucket match (:1744877-1744878).
"""

from __future__ import annotations

from ogtool.features.batch_invite_v2 import (
    InviteV2Config,
    bucket_jobs,
    parse_index_expression,
    run,
)


def test_the_range_expression_parses_terms_and_counts_ignored():
    picked, ignored = parse_index_expression("1-5,7,8", total=8)
    assert picked == [0, 1, 2, 3, 4, 6, 7]
    assert ignored == 0
    picked, ignored = parse_index_expression("2,9,99", total=3)
    assert picked == [1]
    assert ignored == 2
    picked, ignored = parse_index_expression("5-3", total=8)
    assert picked == [2, 3, 4]
    picked, ignored = parse_index_expression("1,,2", total=3)
    assert picked == [0, 1] and ignored == 0
    picked, ignored = parse_index_expression("abc", total=3)
    assert picked == [] and ignored == 1
    picked, ignored = parse_index_expression("", total=3)
    assert picked == [] and ignored == 0


def test_duplicates_inside_one_expression_collapse():
    picked, _ignored = parse_index_expression("1,1,2-3", total=5)
    assert picked == [0, 1, 2]


def test_bucket_jobs_match_one_bucket_to_one_target():
    handlers = ["h1", "h2", "h3", "h4", "h5"]
    jobs = bucket_jobs(
        ["@g1", "@g2"],
        ["m1", "m2", "m3"],
        handlers,
        buckets=[[0, 1], [2, 3, 4]],
    )
    assert [(handler, target) for handler, target, _ in jobs[:3]] == [
        ("h1", "@g1"),
        ("h2", "@g1"),
        ("h1", "@g1"),
    ]
    assert all(target == "@g2" for _h, target, _m in jobs[3:])


def test_a_target_without_a_bucket_stays_on_the_default_pool():
    handlers = ["h1", "h2"]
    jobs = bucket_jobs(["@g1", "@g2"], ["m1", "m2"], handlers, buckets=[[0]])
    pairs = [(handler, target) for handler, target, _ in jobs]
    assert ("h1", "@g1") in pairs
    assert any(target == "@g2" for _, target, _m in jobs)


def test_an_empty_bucket_leaves_its_target_unworked():
    jobs = bucket_jobs(["@g1", "@g2"], ["m1"], ["h1"], buckets=[[], [0]])
    assert jobs == [("h1", "@g2", "m1")]


def test_the_run_prints_the_page_confirmation_lines(ctx, capsys):
    config = InviteV2Config(
        account_labels=["acc1", "acc2"],
        targets=["@g1", "@g2"],
        contacts=["m1"],
        accounts_expression="1\n2",
    )
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert out.count("Selected 1 account indices") == 2
    assert "Loaded 2 valid accounts" in out
    assert "PLAN: 2 invites" in out


def test_ignored_indices_are_reported(ctx, capsys):
    config = InviteV2Config(
        account_labels=["acc1"],
        targets=["@g1"],
        contacts=["m1"],
        accounts_expression="1,9,12",
    )
    assert run(config, ctx) == 0
    out = capsys.readouterr().out
    assert "Ignored targets (no owner account matched): 2" in out
