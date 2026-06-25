"""Generalized WxH-MB craft to test context/slice effects on the acpred=1 DC predictor."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import craft_acpred as C0

Mm = C0.Mm
dcl_inv = C0.dcl_inv
dcc_inv = C0.dcc_inv
mbi = C0.mbi
HDR = C0.HDR
defv = C0.defv


def build_sk(W, H):
    Y = (np.arange(W * H).reshape(H, W) * 7 % 256).astype(np.uint8)
    U = (np.arange(W * H // 4).reshape(H // 2, W // 2) * 13 % 256).astype(np.uint8)
    V = U.copy()
    open("/tmp/skN.yuv", "wb").write(Y.tobytes() + U.tobytes() + V.tobytes())
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
            f"{W}x{H}",
            "-i",
            "/tmp/skN.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            "2",
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            "/tmp/skN.avi",
        ]
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            "/tmp/skN.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            "/tmp/skNf.bin",
        ]
    )
    return open("/tmp/skNf.bin", "rb").read(), bytearray(
        open("/tmp/skN.avi", "rb").read()
    )


def enc(mbw, mbh, lumaDC, ap_mb, coded, tgtblk_diff=None):
    bits = HDR
    dcL = np.full((2 * mbh, 2 * mbw), defv)
    gCb = np.full((mbh, mbw), defv)
    gCr = np.full((mbh, mbw), defv)
    cg = np.zeros((2 * mbh, 2 * mbw), int)
    for my in range(mbh):
        for mx in range(mbw):
            wantc = [1 if (my, mx, b) in coded else 0 for b in range(4)]
            cbpy = ""
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = cg[by][bx - 1] if bx > 0 else 0
                Bb = cg[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                Cc = cg[by - 1][bx] if by > 0 else 0
                pred = A if Bb == Cc else Cc
                cbpy += str(wantc[i] ^ pred)
                cg[by][bx] = wantc[i]
            bits += mbi["00_" + cbpy] + ap_mb[my][mx]
            for blk in range(6):
                if blk < 4:
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                    a = dcL[by][bx - 1] if bx > 0 else defv
                    bb = dcL[by - 1][bx - 1] if (bx > 0 and by > 0) else defv
                    c = dcL[by - 1][bx] if by > 0 else defv
                    fl = abs(a - bb) > abs(bb - c)
                    pred = a if fl else c
                    tgt = lumaDC[by][bx]
                    bits += dcl_inv[tgt - pred]
                    dcL[by][bx] = tgt
                    if wantc[blk]:
                        bits += coded[(my, mx, blk)]
                else:
                    g = gCb if blk == 4 else gCr
                    a = g[my][mx - 1] if mx > 0 else defv
                    bb = g[my - 1][mx - 1] if (mx > 0 and my > 0) else defv
                    c = g[my - 1][mx] if my > 0 else defv
                    fl = abs(a - bb) > abs(bb - c)
                    pred = a if fl else c
                    bits += dcc_inv[128 - pred]
                    g[my][mx] = 128
    return bits
