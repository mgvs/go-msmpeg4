#!/usr/bin/env python3
"""probe_ac.py — single-AC-coefficient probe for reversing the MS-MPEG4 v3 AC
run/level/last VLC (Phase 3).

PROOF-OF-PROVENANCE TOOL (black box only). To map the AC coefficient VLC we need
to feed the encoder blocks containing exactly ONE known AC coefficient. We build
such a block as a single 8x8 DCT basis function basis(u,v) scaled by an amplitude
(its mean is 0 → the DC level stays 0 = `10`), drop it into block 0 of a 16x16
frame, encode to DIV3 and read back the bitstream. Sweeping (u,v) and amplitude
enumerates the (run, level, last) codewords; numpy provides the forward DCT used
only to CHOOSE inputs (never to read the codec's tables).

usage: python3 re/probe_ac.py <out_dir> <u> <v> <amp> <name>
"""
import subprocess
import sys

import numpy as np


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


def encode_block0(d, name, blk8):
    Y = np.full((16, 16), 128.0)
    Y[:8, :8] = 128 + blk8
    Y = np.clip(np.round(Y), 0, 255).astype(np.uint8)
    open(f"{d}/{name}.yuv", "wb").write(Y.tobytes() + bytes([128] * 128))
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
            f"{d}/{name}.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            "8",
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            f"{d}/{name}.avi",
        ]
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            f"{d}/{name}.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            f"{d}/{name}.bin",
        ]
    )


def bits(path):
    with open(path, "rb") as f:
        return "".join(format(x, "08b") for x in f.read())


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "/tmp/msm_craft"
    u, v, amp, name = (
        int(sys.argv[2]),
        int(sys.argv[3]),
        float(sys.argv[4]),
        sys.argv[5],
    )
    encode_block0(d, name, amp * basis(u, v))
    print(bits(f"{d}/{name}.bin"))
