"""Target parsing: the four kinds, links, dedupe and bad input."""

from __future__ import annotations

import pytest

from ogtool.targets import (
    Target,
    TargetError,
    collect,
    parse_entry,
    parse_many,
    split_inline,
)


@pytest.mark.parametrize(
    ("entry", "kind", "ref"),
    [
        ("@demo_chat", "custom", "@demo_chat"),
        ("demo_chat", "custom", "@demo_chat"),
        ("+8613800000000", "custom", "+8613800000000"),
        ("8613800000000", "custom", "+8613800000000"),
        ("-1001234567890", "custom", "-1001234567890"),
        ("https://t.me/demo_public", "custom", "@demo_public"),
        ("t.me/demo_public", "custom", "@demo_public"),
    ],
)
def test_parse_entries(entry, kind, ref):
    target = parse_entry(entry, kind)
    assert isinstance(target, Target)
    assert target.ref == ref


def test_invite_link_becomes_a_hash():
    assert parse_entry("https://t.me/+AbCdEf123456").ref == "+AbCdEf123456"
    assert parse_entry("t.me/joinchat/AbCdEf123456").ref == "+AbCdEf123456"


def test_group_kind_is_kept():
    assert parse_entry("@demo_chat", "group").kind == "group"


def test_comments_are_skipped():
    targets = parse_many(["# a comment", "@demo_chat"])
    assert [t.ref for t in targets] == ["@demo_chat"]


def test_duplicates_are_dropped():
    targets = parse_many(["@demo_chat", "@demo_chat", "t.me/demo_chat"])
    assert len(targets) == 1


def test_bad_entry_raises():
    with pytest.raises(TargetError):
        parse_entry("!!! not a target")


def test_split_inline():
    assert split_inline("@a, @b ,, @c") == ["@a", "@b", "@c"]


def test_collect_merges_inline_and_files(tmp_path):
    listing = tmp_path / "targets.txt"
    listing.write_text("@from_file\n# comment\n", encoding="utf-8")
    targets = collect(inline="@inline", files=[str(listing)])
    assert sorted(t.ref for t in targets) == ["@from_file", "@inline"]


def test_a_bare_number_is_an_id_first_with_a_phone_fallback():
    target = parse_entry("6805350975", "custom")
    assert target.kind == "private"
    assert target.ref == "6805350975"
    assert target.phone_fallback == "+6805350975"


def test_a_leading_plus_always_means_a_phone():
    target = parse_entry("+6805350975", "custom")
    assert target.ref == "+6805350975"
    assert target.phone_fallback == ""


def test_a_number_with_its_country_code_stays_a_phone():
    assert parse_entry("8613800000000", "custom").ref == "+8613800000000"


def test_peer_variants_try_the_id_before_the_phone():
    from ogtool.senders import peer_variants

    assert peer_variants(parse_entry("6805350975")) == [6805350975,
                                                       "+6805350975"]
    assert peer_variants(parse_entry("@demo))".replace("))", ""))) == ["@demo"]
    assert peer_variants(parse_entry("+15550000001")) == ["+15550000001"]


def test_a_private_post_link_becomes_a_numeric_channel_ref():
    """t.me/c/<id>/<post> used to stay "c/<id>" and no resolver could read it.

    The channel's real id is what every consumer needs; the post part is a
    location, not part of the address, so it is dropped.
    """
    target = parse_entry("https://t.me/c/4365539795/2", "custom")
    assert target.ref == "4365539795"
    # A channel link without a post lands in the same place.
    assert parse_entry("https://t.me/c/4365539795", "custom").ref == "4365539795"
    # A bare -100 channel id keeps working as before.
    assert parse_entry("-1004365539795", "custom").ref == "-1004365539795"
