"""Controlled acpred=1 craft: build a valid 2x2-MB (32x32) config-0 I-frame by hand,
all blocks flat (cbp=0, DC only) with chosen DCs, then make MB(1,1)blk0 coded+acpred=1
with one controlled AC code. Decode via ffmpeg oracle -> reverse the acpred=1 logic."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

Mm = np.array(
    [
        [
            0.5
            * ((1 / np.sqrt(2)) if k == 0 else 1)
            * np.cos((2 * n + 1) * k * np.pi / 16)
            for n in range(8)
        ]
        for k in range(8)
    ]
)
dcl = json.load(open("data/dc_luma.json"))
dcl_inv = {v: k for k, v in dcl.items()}
dcc = json.load(open("data/dc_chroma.json"))
dcc_inv = {v: k for k, v in dcc.items()}
mbi = json.load(open("data/table_mb_intra_raw.json"))
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
cfg = "".join(format(x, "08b") for x in open("/tmp/divx/cfg0.bin", "rb").read())
HDR = cfg[:17]  # config-0 q=4 header
ds = 8
defv = 1024 // ds  # q=4


# need a 32x32 skeleton oracle
def build_sk():
    Y = (np.arange(32 * 32).reshape(32, 32) * 7 % 256).astype(np.uint8)
    U = (np.arange(16 * 16).reshape(16, 16) * 13 % 256).astype(np.uint8)
    V = (np.arange(16 * 16).reshape(16, 16) * 17 % 256).astype(np.uint8)
    open("/tmp/sk32.yuv", "wb").write(Y.tobytes() + U.tobytes() + V.tobytes())
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
            "32x32",
            "-i",
            "/tmp/sk32.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            "2",
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            "/tmp/sk32.avi",
        ]
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            "/tmp/sk32.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            "/tmp/sk32f.bin",
        ]
    )


build_sk()
sk32 = open("/tmp/sk32f.bin", "rb").read()
sk32avi = bytearray(open("/tmp/sk32.avi", "rb").read())
sk32off = sk32avi.find(sk32)
print("sk32 frame size:", len(sk32))


def decode(bits):
    by = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits) // 8 * 8, 8))
    fb = bytes(by)
    if len(fb) > len(sk32):
        print("TOO BIG", len(fb))
        return None
    a = bytearray(sk32avi)
    a[sk32off : sk32off + len(sk32)] = fb + sk32[len(fb) :]
    open("/tmp/cr.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/cr.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    if len(o) < 32 * 32 * 3 // 2:
        return None
    return np.frombuffer(o[: 32 * 32], np.uint8).reshape(32, 32).astype(float), o


# build frame: dcL grid 4x4 (luma blocks), target luma DC levels per block
def encode_flat(lumaDC, cbDC, crDC, target=None):
    """lumaDC[by][bx] target DC level (4x4). All cbp=0 unless target. target=(coded blk0 AC bits, acpred)."""
    bits = HDR
    dcL = np.full((4, 4), defv)
    gCb = np.full((2, 2), defv)
    gCr = np.full((2, 2), defv)
    coded = np.zeros((4, 4), int)
    for my in range(2):
        for mx in range(2):
            istarget = target is not None and mx == 1 and my == 1
            if istarget:
                raw = "00_1000"
                bits += mbi[raw]
                ap = target[1]
            else:
                raw = "00_0000"
                bits += mbi[raw]
                ap = "0"
            cbcr, cbpy = raw.split("_")
            cbp = [0] * 6
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = coded[by][bx - 1] if bx > 0 else 0
                Bb = coded[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                Cc = coded[by - 1][bx] if by > 0 else 0
                cbp[i] = int(cbpy[i]) ^ (A if Bb == Cc else Cc)
                coded[by][bx] = cbp[i]
            cbp[4] = int(cbcr[0])
            cbp[5] = int(cbcr[1])
            bits += ap
            for blk in range(6):
                if blk < 4:
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                    a = dcL[by][bx - 1] if bx > 0 else defv
                    bb = dcL[by - 1][bx - 1] if (bx > 0 and by > 0) else defv
                    c = dcL[by - 1][bx] if by > 0 else defv
                    fromleft = abs(a - bb) > abs(bb - c)
                    # use SELECT predictor for flat blocks (acpred=0)
                    pred = a if fromleft else c
                    tgt = lumaDC[by][bx]
                    diff = tgt - pred
                    bits += dcl_inv[diff]
                    dcL[by][bx] = tgt
                else:
                    g = gCb if blk == 4 else gCr
                    a = g[my][mx - 1] if mx > 0 else defv
                    bb = g[my - 1][mx - 1] if (mx > 0 and my > 0) else defv
                    c = g[my - 1][mx] if my > 0 else defv
                    fromleft = abs(a - bb) > abs(bb - c)
                    pred = a if fromleft else c
                    tgt = cbDC if blk == 4 else crDC
                    diff = tgt - pred
                    bits += dcc_inv[diff]
                    g[my][mx] = tgt
                if cbp[blk] and istarget and blk == 0:
                    bits += target[0]  # controlled AC bits (must end last=1)
    return bits


# TEST: all flat luma DC=128 (level 128), verify decode
lumaDC = [[128] * 4 for _ in range(4)]
bits = encode_flat(lumaDC, 128, 128)
res = decode(bits)
if res is None:
    print("decode failed")
else:
    Y, _ = res
    print(
        "all-flat DC=128: decoded block(0,0) mean =",
        round(Y[0:8, 0:8].mean()),
        "(expect 128)",
    )
    print("  block(1,1)-MB(1,1)blk0 at (16,16) mean =", round(Y[16:24, 16:24].mean()))
