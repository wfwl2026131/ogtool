"""Four more page fields: keep online's names, DM v2's cap, group's preview.

``pingDelayMs`` / ``usageAccountIds`` are the keep-online page's own key names;
``perTargetSuccessCap`` and ``previewCount`` are the two caps that were missing;
``collectOwnedSessionsSnapshot`` / ``ensureInviteLinkSnapshot`` are the group
page's two snapshot switches.
"""

from __future__ import annotations

from ogtool.features.filter_group import GroupConfig
from ogtool.features.filter_group import run as run_groups
from ogtool.features.keep_online import KeepOnlineConfig
from ogtool.features.mass_send_v2 import MassV2Config, _cap_per_target


def test_keep_online_reads_the_pages_delay_name():
    config = KeepOnlineConfig.from_dict(
        {"keep_online": {"ping_delay_ms": 45000}})
    assert config.interval == 45.0


def test_keep_online_still_reads_the_seconds_name():
    config = KeepOnlineConfig.from_dict({"keep_online": {"interval": 90}})
    assert config.interval == 90.0


def test_keep_online_reads_usage_accounts():
    config = KeepOnlineConfig.from_dict(
        {"keep_online": {"usage_accounts": ["acc1"]}})
    assert config.account_labels == ["acc1"]


def test_keep_online_falls_back_to_the_plain_accounts_list():
    config = KeepOnlineConfig.from_dict({"accounts": ["acc2"]})
    assert config.account_labels == ["acc2"]


def test_the_per_target_cap_keeps_the_first_messages():
    class Pair:
        def __init__(self, target: str) -> None:
            self.target = target

    pairs = [Pair("@a"), Pair("@a"), Pair("@a"), Pair("@b")]
    kept, dropped = _cap_per_target(pairs, 2)
    assert [pair.target for pair in kept] == ["@a", "@a", "@b"]
    assert dropped == 1


def test_a_cap_of_zero_is_not_applied():
    config = MassV2Config(per_target_success_cap=0, text="hi")
    assert config.per_target_success_cap == 0


def test_dm_v2_reads_the_new_fields():
    config = MassV2Config.from_dict(
        {"mass_send_v2": {"text": "hi", "per_target_success_cap": 3,
                          "request_interval_ms": 2500,
                          "count_existing": True}},
        type("S", (), {"runtime": type("R", (), {
            "clamp_threads": staticmethod(lambda value: value)})()})(),
    )
    assert config.per_target_success_cap == 3
    assert config.request_interval_ms == 2500
    assert config.count_existing is True


def test_group_filter_reads_the_two_snapshots_and_the_preview():
    config = GroupConfig.from_dict(
        {"filter_group": {"groups": "@g1",
                          "preview_count": 2,
                          "collect_owned_sessions": True,
                          "ensure_invite_link": True}},
        None,  # type: ignore[arg-type]
    )
    assert config.preview_count == 2
    assert config.collect_owned_sessions is True
    assert config.ensure_invite_link is True


def test_group_filter_prints_the_preview_and_the_snapshot(ctx, capsys):
    config = GroupConfig(groups=["@g1", "@g2"], preview_count=1,
                         collect_owned_sessions=True,
                         global_interval_ms=0, account_interval_ms=0)
    assert run_groups(config, ctx) == 0
    output = capsys.readouterr().out
    assert "sessions snapshot:" in output
    assert any("预览：" in line for line in output.splitlines())
