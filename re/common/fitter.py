#!/usr/bin/env python3
"""fitter.py — the consume-exactly decoder-fitter for MS-MPEG4 v3 intra MBs.

This is the growing partial decoder we FIT against the crafted-frame corpus +
ffmpeg pixel oracle (black box only). It decodes everything reversed so far and
stops at the first unknown, so we can see exactly where the next field begins.

Reversed so far (all from observed bitstreams — see NOTES.md):
  - picture header: coding(2) + quant(5)
  - MCBPC (intra, cbpc=00) = fixed `1011110111` (10 bits)
  - CBPY-region (bits 17 → block-0 DC), 16-entry table below (may bundle ac_pred)
  - 6 DC, contiguous: 4 luma (dc_luma_table.go) + 2 chroma (dc_chroma_table.go)
  - AC sections follow (TODO: TCOEF run/level/last VLC + EOB) — the fitter stops
    here and prints the remaining bits = the AC payload to reverse next.

Usage: python3 re/fitter.py <dir> <name>
"""
import re
import sys

MCBPC_INTRA_CBPC00 = "1011110111"  # 10 bits, constant for our cbpc=00 corpus

# CBPY-region code -> luma AC pattern (b0,b1,b2,b3). Lengths 2..9 (likely incl.
# a per-MB ac_pred_flag; the full fitter will split it). Derived via DC-offset
# first-diff over all 16 patterns.
CBPY_REGION = {
    "10": (0, 0, 0, 0),
    "001100": (0, 0, 0, 1),
    "0000100": (0, 0, 1, 0),
    "000100": (0, 0, 1, 1),
    "0000010": (0, 1, 0, 0),
    "000110": (0, 1, 0, 1),
    "01000100": (0, 1, 1, 0),
    "0111100": (0, 1, 1, 1),
    "0101010": (1, 0, 0, 0),
    "000011010": (1, 0, 0, 1),
    "0101000": (1, 0, 1, 0),
    "00001110": (1, 0, 1, 1),
    "0111110": (1, 1, 0, 0),
    "00000010": (1, 1, 0, 1),
    "00100100": (1, 1, 1, 0),
    "01100": (1, 1, 1, 1),
}


def load_dc(path):
    m = {}
    for ln in open(path):
        g = re.search(r"\{(\d+), 0b([01]+), (-?\d+)\}", ln)
        if g:
            m[(int(g.group(1)), g.group(2))] = int(g.group(3))
    return m


class Br:
    def __init__(s, data):
        s.b = "".join(format(x, "08b") for x in data)
        s.p = 0

    def take(s, n):
        v = s.b[s.p : s.p + n]
        s.p += n
        return v

    def vlc(s, table, maxlen=24):
        code = ""
        while len(code) < maxlen and s.p + len(code) < len(s.b):
            code += s.b[s.p + len(code)]
            if (len(code), code) in table:
                s.p += len(code)
                return table[(len(code), code)]
        return None

    def prefix(s, table, maxlen=10):
        code = ""
        while len(code) < maxlen and s.p + len(code) < len(s.b):
            code += s.b[s.p + len(code)]
            if code in table:
                s.p += len(code)
                return table[code]
        return None


def decode(d, name):
    dcl = load_dc("dc_luma_table.go")
    dcc = load_dc("dc_chroma_table.go")
    data = open(f"{d}/{name}.bin", "rb").read()
    r = Br(data)
    total = len(data) * 8
    coding, quant = int(r.take(2), 2), int(r.take(5), 2)
    mcbpc = r.take(10)
    assert mcbpc == MCBPC_INTRA_CBPC00, f"unexpected MCBPC {mcbpc}"
    pat = r.prefix(CBPY_REGION, maxlen=10)
    dcs = [r.vlc(dcl if i < 4 else dcc) for i in range(6)]
    rem = r.b[r.p :]
    print(f"{name}: total={total} coding={coding} quant={quant} cbpy={pat} DC={dcs}")
    print(f"   AC payload (from bit {r.p}): {rem}")
    return dcs, rem


if __name__ == "__main__":
    decode(
        sys.argv[1] if len(sys.argv) > 1 else "/tmp/msm_craft",
        sys.argv[2] if len(sys.argv) > 2 else "a128",
    )
