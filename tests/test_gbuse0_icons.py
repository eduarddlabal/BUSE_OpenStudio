from busepanel.core.fonts import default_codetable
from busepanel.gbuse0 import mnemo as M


def test_visual_editor_encodes_icons():
    ct = default_codetable()
    b, probs = M._encode_str("Hlavní nádraží {icon:vlak}", ct)
    assert not probs and b.endswith(b" \xad")
    assert "{icon:vlak}" in M.to_mnemo(b, ct)
    assert M._decode_txt(b, ct) == "Hlavní nádraží {icon:vlak}"
    b2, probs2 = M.from_mnemo(M.to_mnemo(b, ct), ct)
    assert b2 == b and not probs2


def test_exterior_pid_pictograms_and_big_metro():
    from busepanel.core.icons import ICON_CODES, exterior_icon_rows
    from busepanel.peripherals import exterior as X
    for name in ("metro", "vlak", "letadlo", "privoz", "vyluka"):
        assert len(exterior_icon_rows(name, 19)) == 19
    assert exterior_icon_rows("letadlo", 19)[0] == "." + "#" * 26 + "."    # standard PID 28×19
    db = X.variant_database("v1")
    fld = db.fields["dest"]
    codes = X.encode_text("Bořislavka {icon:metro_l}A{icon:metro_r}")
    rec = X.autoformat(db, fld, codes)
    big = X.font_height(db, rec[0] - 0xE0) >= X.BIG_METRO_H
    assert (bytes([ICON_CODES["metro"]]) in rec) == big
    assert (bytes([ICON_CODES["metro_l"]]) in rec) != big
    assert big                                  # 19-row destination -> M pictogram
    small = X.autoformat(db, fld, bytes([0xE0 + 3]) + codes)     # 9-row font 3 -> line letter [A]
    assert bytes([ICON_CODES["metro_l"]]) + b"A" in small
