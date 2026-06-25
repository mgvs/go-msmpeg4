"""General craft: 2x2 MB, per-block (cbp, AC) control. Test if neighbor AC changes
the acpred=1 target block's DC predictor."""

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


def enc(lumaDC, acpred_mb, coded):
    """lumaDC[by][bx]=4x4 target DC. acpred_mb[my][mx]. coded[(my,mx,blk)]=acbits (makes that luma blk coded)."""
    bits = HDR
    dcL = np.full((4, 4), defv)
    gCb = np.full((2, 2), defv)
    gCr = np.full((2, 2), defv)
    codedg = np.zeros((4, 4), int)
    for my in range(2):
        for mx in range(2):
            # which luma blocks coded this MB
            wantc = [1 if (my, mx, b) in coded else 0 for b in range(4)]
            # raw cbpy = wantc XOR pred (so decoder recovers wantc); pred from codedg
            cbpy = ""
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = codedg[by][bx - 1] if bx > 0 else 0
                Bb = codedg[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                Cc = codedg[by - 1][bx] if by > 0 else 0
                pred = A if Bb == Cc else Cc
                cbpy += str(wantc[i] ^ pred)
                codedg[by][bx] = wantc[i]  # update per-block like decoder
            raw = "00_" + cbpy
            bits += mbi[raw]
            bits += acpred_mb[my][mx]
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


def dec_dc(bits, bx, by):
    by_ = bytearray(int(bits[i : i + 8], 2) for i in range(0, len(bits) // 8 * 8, 8))
    a = bytearray(C0.sk32avi)
    a[C0.sk32off : C0.sk32off + len(C0.sk32)] = bytes(by_) + C0.sk32[len(by_) :]
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
    if len(o) < 32 * 32:
        return None
    Y = np.frombuffer(o[: 32 * 32], np.uint8).reshape(32, 32).astype(float)
    return Mm @ Y[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T


c1511 = rl[(15, 1, 1)]
# neighbors a=213(bx1,by2) bb=216(bx1,by1) c=220(bx2,by1); target blk0(bx2,by2) diff0
luma = [[200] * 4 for _ in range(4)]
luma[2][1] = 213
luma[1][1] = 216
luma[1][2] = 220
luma[2][2] = 220
ap = [["0", "0"], ["0", "1"]]  # MB(1,1) acpred=1
# CASE A: flat neighbors
bits = enc(luma, ap, {(1, 1, 0): c1511 + "0"})
C = dec_dc(bits, 2, 2)
print(f"A flat neighbors: target DC={round(C[0][0]/8)} (expect select 220)")
# CASE B: c-neighbor MB(1,0)blk2 coded with first-row AC (0,1,1)
o011 = rl[(0, 1, 1)]
bits = enc(luma, ap, {(1, 1, 0): c1511 + "0", (0, 1, 2): o011 + "0"})
C = dec_dc(bits, 2, 2)
Cc = dec_dc(bits, 2, 1)
print(
    f"B c-neighbor has (0,1,1) row-AC: c-block now ({round(Cc[0][0]/8)},(0,1)={round(Cc[0][1])}) target DC={round(C[0][0]/8)}"
)
