"""Faithful re-encoder: extract every block's quantized coefficients from the oracle
YUV, re-encode rows 0-1 as acpred=0 (exact reconstruction), target MB(3,1) acpred=1.
If ffmpeg then gives 218 at MB(3,1), the quirk is reproducible & controllable."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import craft_acpred as C0

Mm = C0.Mm
dcl_inv = C0.dcl_inv
dcc_inv = C0.dcc_inv
mbi = C0.mbi
HDR = C0.HDR
defv = C0.defv
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
rlc = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_chroma.json")).items()
}
zigzag = [
    0,
    1,
    8,
    16,
    9,
    2,
    3,
    10,
    17,
    24,
    32,
    25,
    18,
    11,
    4,
    5,
    12,
    19,
    26,
    33,
    40,
    48,
    41,
    34,
    27,
    20,
    13,
    6,
    7,
    14,
    21,
    28,
    35,
    42,
    49,
    56,
    57,
    50,
    43,
    36,
    29,
    22,
    15,
    23,
    30,
    37,
    44,
    51,
    58,
    59,
    52,
    45,
    38,
    31,
    39,
    46,
    53,
    60,
    61,
    54,
    47,
    55,
    62,
    63,
]
o = open("/tmp/divx/cfg0_true.yuv", "rb").read()
W, H = 512, 288
Yt = np.frombuffer(o[: W * H], np.uint8).reshape(H, W).astype(float)
Cbt = (
    np.frombuffer(o[W * H : W * H + W * H // 4], np.uint8)
    .reshape(H // 2, W // 2)
    .astype(float)
)
Crt = (
    np.frombuffer(o[W * H + W * H // 4 :], np.uint8)
    .reshape(H // 2, W // 2)
    .astype(float)
)
q = 4
ds = 8


def quant_block(plane, r, c):
    Cf = Mm @ plane[r : r + 8, c : c + 8] @ Mm.T
    levels = {}
    for u in range(8):
        for v in range(8):
            F = Cf[u][v]
            if u == 0 and v == 0:
                levels[(0, 0)] = round(F / ds)
                continue
            if abs(F) < q:
                continue
            L = int(np.sign(F) * round((abs(F) / q - 1) / 2))
            if L:
                levels[(u, v)] = L
    return levels


def enc_ac(levels, tab):
    items = sorted(
        [
            (zigzag.index(u * 8 + v), l)
            for (u, v), l in levels.items()
            if (u, v) != (0, 0)
        ]
    )
    if not items:
        return None  # no AC -> cbp=0
    bits = ""
    for i, (pos, l) in enumerate(items):
        run = pos - (items[i - 1][0] if i > 0 else 0) - 1
        last = 1 if i == len(items) - 1 else 0
        key = (run, abs(l), last)
        if key not in tab:
            return "UNENC"
        bits += tab[key] + ("1" if l < 0 else "0")
    return bits


def build(mbw, mbh, target=(3, 1), keepac=None, apflags=None):
    bits = HDR
    dcL = np.full((2 * mbh, 2 * mbw), defv)
    gCb = np.full((mbh, mbw), defv)
    gCr = np.full((mbh, mbw), defv)
    cg = np.zeros((2 * mbh, 2 * mbw), int)
    for my in range(mbh):
        for mx in range(mbw):
            istgt = (mx, my) == target
            # gather 6 blocks' levels
            blklv = []
            for blk in range(6):
                if blk < 4:
                    lv = quant_block(
                        Yt, (2 * my + blk // 2) * 8, (2 * mx + blk % 2) * 8
                    )
                elif blk == 4:
                    lv = quant_block(Cbt, my * 8, mx * 8)
                else:
                    lv = quant_block(Crt, my * 8, mx * 8)
                blklv.append(lv)
            acbits = []
            wantc = []
            for blk in range(6):
                tab = rl if blk < 4 else rlc
                if keepac is not None and not keepac(mx, my, blk):
                    ab = None
                else:
                    ab = enc_ac(blklv[blk], tab)
                    if ab == "UNENC":
                        ab = None
                acbits.append(ab)
                wantc.append(1 if ab else 0)
            # cbp prediction for luma
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
            cbcr = str(wantc[4]) + str(wantc[5])
            raw = cbcr + "_" + cbpy
            if raw not in mbi:
                return None, f"raw {raw} not in mbi @MB({mx},{my})"
            ap = "1" if istgt else (apflags(mx, my) if apflags else "0")
            bits += mbi[raw] + ap
            for blk in range(6):
                if blk < 4:
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                    a = dcL[by][bx - 1] if bx > 0 else defv
                    bb = dcL[by - 1][bx - 1] if (bx > 0 and by > 0) else defv
                    c = dcL[by - 1][bx] if by > 0 else defv
                    fl = abs(a - bb) > abs(bb - c)
                    pred = a if fl else c
                    tgt = blklv[blk][(0, 0)]
                    if istgt and blk == 0:
                        bits += dcl_inv[0]
                        dcL[by][bx] = pred  # FORCE diff0 like real cfg0
                    else:
                        if tgt - pred not in dcl_inv:
                            return (
                                None,
                                f"dc diff {tgt-pred} unenc @MB({mx},{my})blk{blk}",
                            )
                        bits += dcl_inv[tgt - pred]
                        dcL[by][bx] = tgt
                    if wantc[blk]:
                        bits += acbits[blk]
                else:
                    g = gCb if blk == 4 else gCr
                    a = g[my][mx - 1] if mx > 0 else defv
                    bb = g[my - 1][mx - 1] if (mx > 0 and my > 0) else defv
                    c = g[my - 1][mx] if my > 0 else defv
                    fl = abs(a - bb) > abs(bb - c)
                    pred = a if fl else c
                    tgt = blklv[blk][(0, 0)]
                    if tgt - pred not in dcc_inv:
                        return None, f"dcc diff @MB({mx},{my})"
                    bits += dcc_inv[tgt - pred]
                    g[my][mx] = tgt
                    if wantc[blk]:
                        bits += acbits[blk]
    return bits, None


mbw, mbh = 8, 2


def decsk():
    Y = (np.arange(mbw * 16 * mbh * 16).reshape(mbh * 16, mbw * 16) * 7 % 256).astype(
        np.uint8
    )
    U = (
        np.arange(mbw * 16 * mbh * 16 // 4).reshape(mbh * 8, mbw * 8) * 13 % 256
    ).astype(np.uint8)
    V = U.copy()
    open("/tmp/re.yuv", "wb").write(Y.tobytes() + U.tobytes() + V.tobytes())
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
            f"{mbw*16}x{mbh*16}",
            "-i",
            "/tmp/re.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            "2",
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            "/tmp/re.avi",
        ]
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            "/tmp/re.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            "/tmp/ref.bin",
        ]
    )
    return open("/tmp/ref.bin", "rb").read(), bytearray(
        open("/tmp/re.avi", "rb").read()
    )


sk, skavi = decsk()
skoff = skavi.find(sk)


def run(keepac, lbl):
    bits, err = build(mbw, mbh, keepac=keepac)
    if err:
        print(f"  {lbl}: ERR {err}")
        return
    byts = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits) // 8 * 8, 8))
    if len(byts) > len(sk):
        print(f"  {lbl}: too big")
        return
    a = bytearray(skavi)
    a[skoff : skoff + len(sk)] = bytes(byts) + sk[len(byts) :]
    open("/tmp/rec.avi", "wb").write(a)
    oo = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/rec.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    Y = (
        np.frombuffer(oo[: mbw * 16 * mbh * 16], np.uint8)
        .reshape(mbh * 16, mbw * 16)
        .astype(float)
    )
    Cc = Mm @ Y[16:24, 48:56] @ Mm.T
    print(f"  {lbl}: MB(3,1)b0 DC={round(Cc[0][0]/8)}")


import subprocess as sp

for w in [4, 5, 6, 7, 8, 10]:
    mbw = w
    Y = (np.arange(mbw * 16 * mbh * 16).reshape(mbh * 16, mbw * 16) * 7 % 256).astype(
        np.uint8
    )
    U = (
        np.arange(mbw * 16 * mbh * 16 // 4).reshape(mbh * 8, mbw * 8) * 13 % 256
    ).astype(np.uint8)
    V = U.copy()
    open("/tmp/re.yuv", "wb").write(Y.tobytes() + U.tobytes() + V.tobytes())
    sp.run(
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
            f"{mbw*16}x{mbh*16}",
            "-i",
            "/tmp/re.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            "2",
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            "/tmp/re.avi",
        ]
    )
    sp.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            "/tmp/re.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            "/tmp/ref.bin",
        ]
    )
    sk = open("/tmp/ref.bin", "rb").read()
    skavi = bytearray(open("/tmp/re.avi", "rb").read())
    skoff = skavi.find(sk)
    bits, err = build(
        mbw,
        mbh,
        keepac=lambda mx, my, blk: True,
        apflags=lambda mx, my: (
            "1" if (my == 0 and mx in (0, 3, 6, 13, 15, 16, 17, 18)) else "0"
        ),
    )
    if err:
        print(f"  W={w}: ERR {err}")
        continue
    byts = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits) // 8 * 8, 8))
    if len(byts) > len(sk):
        print(f"  W={w}: too big")
        continue
    a = bytearray(skavi)
    a[skoff : skoff + len(sk)] = bytes(byts) + sk[len(byts) :]
    open("/tmp/rec.avi", "wb").write(a)
    oo = sp.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/rec.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    Yd = (
        np.frombuffer(oo[: mbw * 16 * mbh * 16], np.uint8)
        .reshape(mbh * 16, mbw * 16)
        .astype(float)
    )
    dc = round((Mm @ Yd[16:24, 48:56] @ Mm.T)[0][0] / 8)
    # also dump neighbor DCs as decoded
    a2 = round((Mm @ Yd[16:24, 40:48] @ Mm.T)[0][0] / 8)
    c2 = round((Mm @ Yd[8:16, 48:56] @ Mm.T)[0][0] / 8)
    bb2 = round((Mm @ Yd[8:16, 40:48] @ Mm.T)[0][0] / 8)
    print(f"  W={w}: target DC={dc} | decoded neighbors a={a2} bb={bb2} c={c2}")
