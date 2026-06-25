"""Clean DC-quirk data from the REAL config-0 keyframe. Consumption is clean (whole frame
parses), so pred = oracle_DC - read_diff is the TRUE predictor. Neighbours from the ORACLE
(ffmpeg decode), not the drifting grid. For each ap=1 top-row luma block (blk0/blk1,
non-edge): record (pred, left,top,topleft oracle DCs, blk). Then fit the rule."""

import numpy as np, json, sys, pickle, collections

sys.path.insert(0, ".")
import clean_recon as CR  # reuse dec_tcoef/dcdec/tables

mb_intra = CR.mb_intra
dctab_l = CR.dctab_l
dctab_c = CR.dctab_c
Mm = CR.Mm


def odc(Yo, bx, by):
    return round((Mm @ Yo[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T)[0][0] / 8)


def extract(binf, W, H, oraclef):
    b = "".join(format(x, "08b") for x in open(binf, "rb").read())
    q = int(b[2:7], 2)
    o = open(oraclef, "rb").read()
    Yo = np.frombuffer(o[: W * H], np.uint8).reshape(H, W).astype(float)
    mbw, mbh = W // 16, H // 16
    p = 17
    codedL = np.zeros((2 * mbh, 2 * mbw), int)
    pts = []
    for my in range(mbh):
        for mx in range(mbw):
            m = None
            for L in range(1, 14):
                if b[p : p + L] in mb_intra:
                    m = b[p : p + L]
                    break
            if m is None:
                return pts, f"MBfail@{p}({mx},{my})"
            cbpk = mb_intra[m]
            p += len(m)
            cbcr, cbpy = cbpk.split("_")
            raw = [int(cbpy[i]) for i in range(4)] + [int(cbcr[0]), int(cbcr[1])]
            cbp = [0] * 6
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = codedL[by][bx - 1] if bx > 0 else 0
                Bb = codedL[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                Cc = codedL[by - 1][bx] if by > 0 else 0
                cbp[i] = raw[i] ^ (A if Bb == Cc else Cc)
                codedL[by][bx] = cbp[i]
            cbp[4] = raw[4]
            cbp[5] = raw[5]
            acpred = b[p]
            p += 1
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                diff, n = CR.dcdec(b, p, tab)
                if diff is None:
                    return pts, f"DCfail@{p}({mx},{my})b{blk}"
                p += n
                if blk < 4:
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                    if blk in (0, 1) and acpred == "1" and bx > 0 and by > 0:
                        o_ = odc(Yo, bx, by)
                        L_ = odc(Yo, bx - 1, by)
                        T_ = odc(Yo, bx, by - 1)
                        TL_ = odc(Yo, bx - 1, by - 1)
                        pts.append(
                            dict(
                                blk=blk,
                                pred=o_ - diff,
                                o=o_,
                                left=L_,
                                top=T_,
                                topleft=TL_,
                            )
                        )
                if cbp[blk]:
                    chroma = blk >= 4
                    while True:
                        dt = CR.dec_tcoef(b, p, chroma)
                        if dt is None:
                            return pts, f"ACfail@{p}({mx},{my})b{blk}"
                        (run, lev, last), ln = dt
                        p += ln
                        if last:
                            break
    return pts, "OK-full-frame"


pts, status = extract("/tmp/divx/cfg0.bin", 512, 288, "/tmp/divx/cfg0_true.yuv")
print(f"status={status}, {len(pts)} ap=1 top-row DC points (CLEAN, real config-0)")
pickle.dump(pts, open("/tmp/dc_real.pkl", "wb"))


# quick fit
def fl_of(d):
    return abs(d["left"] - d["topleft"]) > abs(d["top"] - d["topleft"])


for blk in (0, 1):
    g = [d for d in pts if d["blk"] == blk]
    if not g:
        continue
    sel = avg = pln = both = 0
    for d in g:
        fl = fl_of(d)
        L, T, TL, pr = d["left"], d["top"], d["topleft"], d["pred"]
        s = L if fl else T
        a = (L + TL) // 2 if fl else (T + TL) // 2
        pl = L + T - TL
        ms = abs(pr - s) <= 1
        ma = abs(pr - a) <= 1
        mp = abs(pr - pl) <= 1
        sel += ms
        avg += ma
        pln += mp
    print(f"blk{blk} ({len(g)}): SELECT={sel} AVG={avg} PLANAR={pln}")
