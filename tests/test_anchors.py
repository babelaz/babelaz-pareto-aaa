"""The judge gets three labelled era anchors per preset, in order: dated (6), clean (7), current (8)."""

import struct

from babelaz_pareto_aaa import check, judge


def test_three_anchor_pngs_per_preset_at_most_512_px():
    for preset in check.PRESETS:
        dated, clean, current, ah = check.anchors(preset)
        for png in (dated, clean, current):
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
            width, height = struct.unpack(">II", png[16:24])
            assert max(width, height) <= 512
        assert len({dated, clean, current}) == 3
        assert ah == check.sha256(dated + clean + current)


def test_judge_text_labels_the_anchors_in_image_order():
    text = judge.judge_text(["E1"], {})
    order = [text.index(s) for s in ("DATED anchor (a 6", "CLEAN anchor (a 7", "CURRENT anchor (an 8")]
    assert order == sorted(order)
