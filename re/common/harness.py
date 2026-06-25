#!/usr/bin/env python3
"""harness.py — decode-and-consume-exactly harness for MS-MPEG4 v3 intra MBs.

PROOF-OF-PROVENANCE / RE TOOL. Decodes a single 16x16 intra MB with HYPOTHESISED
field structure + tables and reports exactly how many bits each field consumes, so
the layout (ac_pred_flag, CBPY, per-block DC+AC+EOB) can be pinned by requiring the
decoder to land on the frame's content end (pad <= 7 bits) and reconstruct == the
ffmpeg pixel oracle. Black box only; tables come from the crafted-bitstream RE.

Usage: python3 re/harness.py <dir> <name>   (decodes <dir>/<name>.bin verbosely)
"""
import re
import sys


def load_dc(path, var):
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

    def u(s, n):
        v = int(s.b[s.p : s.p + n], 2) if n else 0
        s.p += n
        return v

    def show(s, n):
        return s.b[s.p : s.p + n]

    def vlc(s, table, maxlen=24):
        code, n = "", 0
        while n < maxlen:
            code += s.b[s.p + n]
            n += 1
            if (n, code) in table:
                s.p += n
                return table[(n, code)], code
        return None, None


def main(d, name):
    dcl = load_dc("dc_luma_table.go", "luma")
    dcc = load_dc("dc_chroma_table.go", "chroma")
    data = open(f"{d}/{name}.bin", "rb").read()
    r = Br(data)
    total = len(data) * 8
    print(f"{name}: {total} bits total")
    print(f"  header: coding={r.u(2)} quant={r.u(5)} (pos={r.p})")
    print(f"  MCBPC(10)={r.u(10):010b} hypothesis intra/cbpc00 (pos={r.p})")
    # everything from here is the unknown region; dump it and the DC anchors
    print(f"  next 30 bits: {r.show(30)}")
    # Try: decode 6 DC (4 luma, 2 chroma) right after a CBPY of length L; scan L.
    for cbpylen in range(0, 9):
        save = r.p
        r.p = save  # reset
        base = r.p
        r.u(cbpylen)  # skip hypothetical CBPY
        ok = True
        dcs = []
        for blk in range(6):
            tab = dcl if blk < 4 else dcc
            lvl, code = r.vlc(tab)
            if lvl is None:
                ok = False
                break
            dcs.append(lvl)
        if ok:
            print(
                f"  [CBPY={cbpylen}b] 6 DC = {dcs}  -> pos={r.p}, remaining={total - r.p} bits: {r.b[r.p:]}"
            )
        r.p = base


if __name__ == "__main__":
    main(
        sys.argv[1] if len(sys.argv) > 1 else "/tmp/msm_craft",
        sys.argv[2] if len(sys.argv) > 2 else "a128",
    )
