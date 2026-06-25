#!/usr/bin/env python3
"""sweep_tcoef.py — enumerate the MS-MPEG4 v3 intra AC TCOEF VLC (Phase 3).
Black box only (probe_ac-style). Robust AC isolation: decode the block-0 DC with
the already-derived luma DC table so the AC code start is exact (no ±1 wobble),
then take AC = [after DC : block-0 end] where block-0 end = the common
blocks-1..5 suffix. cbpy=1000 (only block 0 has AC) ⇒ CBPY region = 7 bits."""
import os, re, subprocess, sys
import numpy as np

D = sys.argv[1] if len(sys.argv) > 1 else "/tmp/msm_craft"
HDR, MCBPC, CBPY1000 = 7, 10, 7  # bit offsets before block-0 DC for cbpy=1000

# load DC luma VLC from the generated Go table -> {(len,code):level}
DCL = {}
for ln in open("dc_luma_table.go"):
    m = re.search(r"\{(\d+), 0b([01]+), (-?\d+)\}", ln)
    if m:
        DCL[(int(m.group(1)), m.group(2))] = int(m.group(3))


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
            "8",
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


# blocks-1..5 suffix: common tail across single-block-AC frames (block0 varies)
def acmid(name, suffix):
    s = strip(bits(name))
    p = HDR + MCBPC + CBPY1000
    # decode DC luma at p
    code, n = "", 0
    while n < 20:
        code += s[p + n]
        n += 1
        if (n, code) in DCL:
            break
    acstart = p + n
    return s[acstart : len(s) - suffix]


if __name__ == "__main__":
    # establish suffix from a set of run=0 frames (level varied)
    names = []
    for amp in (10, 16, 25, 40):
        nm = f"sw_pos01_{amp}"
        enc(nm, amp * basis(0, 1))
        names.append(nm)
    S = [strip(bits(n)) for n in names]
    suf = min(len(a) for a in S)
    n = 0
    while all(len(s) > n for s in S) and len({s[-1 - n] for s in S}) == 1:
        n += 1
    suffix = n
    print(f"blocks1-5 suffix = {suffix} bits")
    # sweep first zigzag positions (u,v), level ~ amp, read (run,last=1) code
    POS = [
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
    ]
    print(f"{'(u,v)':<7} {'amp':>4}  AC code (single coeff, last=1)")
    for u, v in POS:
        for amp in (12, 24):
            nm = f"sw_{u}{v}_{amp}"
            enc(nm, amp * basis(u, v))
            print(f"  ({u},{v})  {amp:>4}  {acmid(nm, suffix)}")
