#!/usr/bin/env python3
"""gen_tcoef.py — enumerate the MS-MPEG4 v3 intra AC coefficient VLC (TCOEF) and
emit tcoef_table.go.  PROOF-OF-PROVENANCE (black box only).

Method (no AC-prediction model needed): put coefficients only in BLOCK 0 (top-left,
no neighbours → no AC prediction → coded AC == actual coefficients). Each codeword
isolates between the pinned AC-start (bit 27 for cbpy=1000) and the constant
blocks-1..5 suffix. (run,level,last) is read from the ffmpeg pixel oracle via the
confirmed dequant |coef| = quant*(2*level+1); scan = standard zigzag.

Sweep: single coeff at zigzag position p → (run=p-1, level, last=1); a coeff at p
plus a trailing coeff → (run, level, last=0).
"""
import subprocess
import sys

import numpy as np

D = sys.argv[1] if len(sys.argv) > 1 else "/tmp/msm_craft"
AC0 = 27
Q = 8

ZIGZAG = [  # (row,col) = (u,v) in scan order, position 0 = DC
    (0, 0),
    (0, 1),
    (1, 0),
    (2, 0),
    (1, 1),
    (0, 2),
    (0, 3),
    (1, 2),
    (2, 1),
    (3, 0),
    (4, 0),
    (3, 1),
    (2, 2),
    (1, 3),
    (0, 4),
    (0, 5),
    (1, 4),
    (2, 3),
    (3, 2),
    (4, 1),
    (5, 0),
    (6, 0),
    (5, 1),
    (4, 2),
    (3, 3),
    (2, 4),
    (1, 5),
    (0, 6),
    (0, 7),
    (1, 6),
    (2, 5),
    (3, 4),
    (4, 3),
    (5, 2),
    (6, 1),
    (7, 0),
]


def basis(u, v):
    B = np.zeros((8, 8))
    for x in range(8):
        for y in range(8):
            cu = (1 / np.sqrt(2)) if u == 0 else 1
            cv = (1 / np.sqrt(2)) if v == 0 else 1
            B[x, y] = (
                0.5
                * cu
                * cv
                * np.cos((2 * x + 1) * u * np.pi / 16)
                * np.cos((2 * y + 1) * v * np.pi / 16)
            )
    return B


def dct2(b):
    M = np.zeros((8, 8))
    for u in range(8):
        for x in range(8):
            cu = (1 / np.sqrt(2)) if u == 0 else 1
            M[u, x] = 0.5 * cu * np.cos((2 * x + 1) * u * np.pi / 16)
    return M @ b @ M.T


def enc(name, blk):
    Y = np.full((16, 16), 128.0)
    Y[:8, :8] = 128 + blk
    Y = np.clip(np.round(Y), 0, 255).astype(np.uint8)
    open(f"{D}/{name}.yuv", "wb").write(Y.tobytes() + bytes([128] * 128))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-s",
            "16x16",
            "-i",
            f"{D}/{name}.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            str(Q),
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            f"{D}/{name}.avi",
        ]
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            f"{D}/{name}.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            f"{D}/{name}.bin",
        ]
    )


def bits(name):
    return "".join(format(x, "08b") for x in open(f"{D}/{name}.bin", "rb").read())


def strip(s):
    k = 0
    while k < 7 and s.endswith("0"):
        s, k = s[:-1], k + 1
    return s


def coeffs(name):
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            f"{D}/{name}.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    Y = np.frombuffer(o[:256], dtype=np.uint8).reshape(16, 16).astype(float)
    return dct2(Y[:8, :8] - 128)


def level_of(coef):
    if abs(coef) < Q:  # dead-zone
        return 0
    return int(round((abs(coef) / Q - 1) / 2)) * (1 if coef > 0 else -1)


def sweep():
    table = {}  # (run, level, last) -> code(without sign), via magnitude
    # ---- last = 1 : single coefficient at scan position p ----
    # sweep amplitude finely; the oracle tells us the actual level (the basis is
    # scaled ~2.1x vs the codec coefficient, so we don't guess level from amp).
    maxpos = int(sys.argv[2]) if len(sys.argv) > 2 else 17  # run 0..maxpos-2
    for p in range(1, min(maxpos, len(ZIGZAG))):
        u, v = ZIGZAG[p]
        run = p - 1
        names = []
        for amp in range(6, 72, 3):
            nm = f"tc_{p}_{amp}"
            enc(nm, amp * basis(u, v))
            names.append(nm)
        # anchor: common blocks-1..5 suffix across this position's level frames
        S = {nm: strip(bits(nm)) for nm in names}
        ns = list(S.values())
        suf = len(ns[0])
        for i in range(len(ns[0])):
            if len({s[-1 - i] for s in ns if len(s) > i}) != 1:
                suf = i
                break
        for nm in names:
            C = coeffs(nm)
            nz = [
                (uu, vv, C[uu, vv])
                for uu in range(8)
                for vv in range(8)
                if (uu, vv) != (0, 0) and abs(C[uu, vv]) >= Q
            ]
            if len(nz) != 1:
                continue  # not a clean single coefficient
            L = level_of(nz[0][2])
            if L == 0:
                continue
            full = S[nm][AC0 : len(S[nm]) - suf]
            if not full:
                continue
            sign = full[-1]
            mag = full[:-1]
            key = (run, abs(L), 1)
            if key not in table:
                table[key] = mag
    return table


if __name__ == "__main__":
    t = sweep()
    print(f"// {len(t)} TCOEF (run,|level|,last=1) magnitude codes", file=sys.stderr)
    out = sys.stdout
    out.write(
        "package msmpeg4\n\n// Code generated by re/gen_tcoef.py — DO NOT EDIT.\n"
    )
    out.write(
        "// MS-MPEG4 v3 intra AC coefficient VLC, reverse-engineered from block-0\n"
    )
    out.write(
        "// codes (no AC prediction there). {run, level, last, code-len, code}; sign bit follows.\n"
    )
    out.write("var tcoefVLC = []tcoefCode{\n")
    for run, lvl, last in sorted(t):
        c = t[(run, lvl, last)]
        out.write(f"\t{{{run}, {lvl}, {last}, {len(c)}, 0b{c}}},\n")
    out.write("}\n")
