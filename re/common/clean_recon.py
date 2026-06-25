"""Full reconstruction of a real DIV3 I-frame with the clean tables, compared to the
ffmpeg pixel oracle per-MB. The first wrong code shows up as the MB where MSE jumps."""

import numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

mbi = json.load(open("data/table_mb_intra_raw.json"))
mb_intra = {v: k for k, v in mbi.items()}
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
code2rl = {v: k for k, v in rl.items()}
rlc = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_chroma.json")).items()
}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
code2rlc = {v: k for k, v in rlc.items()}
maxlevc = {}
for r, l, la in rlc:
    maxlevc[(la, r)] = max(maxlevc.get((la, r), 0), l)
maxlev = {}
for r, l, la in rl:
    maxlev[(la, r)] = max(maxlev.get((la, r), 0), l)
# scans (raster idx per scan position)
ZZ = [(0, 0)] + R.ZZ_AC
zigzag = [u * 8 + v for u, v in ZZ]
alt_h = [
    0,
    1,
    2,
    3,
    8,
    9,
    16,
    17,
    10,
    11,
    4,
    5,
    6,
    7,
    15,
    14,
    13,
    12,
    19,
    18,
    24,
    25,
    32,
    33,
    26,
    27,
    20,
    21,
    22,
    23,
    28,
    29,
    30,
    31,
    34,
    35,
    40,
    41,
    48,
    49,
    42,
    43,
    36,
    37,
    38,
    39,
    44,
    45,
    46,
    47,
    50,
    51,
    56,
    57,
    58,
    59,
    52,
    53,
    54,
    55,
    60,
    61,
    62,
    63,
]
alt_v = [
    0,
    8,
    16,
    24,
    1,
    9,
    2,
    10,
    17,
    25,
    32,
    40,
    48,
    56,
    57,
    49,
    41,
    33,
    26,
    18,
    3,
    11,
    4,
    12,
    19,
    27,
    34,
    42,
    50,
    58,
    35,
    43,
    51,
    59,
    20,
    28,
    5,
    13,
    6,
    14,
    21,
    29,
    36,
    44,
    52,
    60,
    37,
    45,
    53,
    61,
    22,
    30,
    7,
    15,
    23,
    31,
    38,
    46,
    54,
    62,
    39,
    47,
    55,
    63,
]
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


def idct(F):
    return Mm.T @ F @ Mm


def match_direct(b, p, cm=None):
    cm = cm or code2rl
    for L in range(1, 17):
        if b[p : p + L] in cm:
            return cm[b[p : p + L]], L
    return None, 0


def dec_tcoef(b, p, chroma=False):
    cm = code2rlc if chroma else code2rl
    ml = maxlevc if chroma else maxlev
    esc = "101101001" if chroma else "0000011"
    el = len(esc)
    if b[p : p + el] == esc:
        q = p + el
        if b[q] == "1":
            q += 1
            m = 1
        elif b[q : q + 2] == "01":
            q += 2
            m = 2
        else:
            q += 2
            last = int(b[q])
            run = int(b[q + 1 : q + 7], 2)
            lv = int(b[q + 7 : q + 15], 2)
            return (run, lv - 256 if lv >= 128 else lv, last), (q + 15) - p
        rl_, L = match_direct(b, q, cm)
        if rl_ is None:
            return None
        run, lev, last = rl_
        q += L
        sign = b[q]
        q += 1
        if m == 1:
            lev += ml.get((last, run), 0)
        return (run, -lev if sign == "1" else lev, last), q - p
    rl_, L = match_direct(b, p, cm)
    if rl_ is None:
        return None
    run, lev, last = rl_
    sign = b[p + L]
    return (run, -lev if sign == "1" else lev, last), L + 1


def dcdec(b, p, tab):
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in tab:
            return tab[c], n
    return None, 0


def dequant_ac(level, q):
    if level == 0:
        return 0
    v = q * (2 * abs(level) + 1)
    if q % 2 == 0:
        v -= 1
    return -v if level < 0 else v


def dcscaler(q):
    return 8 if q <= 4 else (2 * q if q <= 8 else (q + 8 if q <= 24 else 2 * q - 16))


def decode(binf, W, H, oraclef):
    b = "".join(format(x, "08b") for x in open(binf, "rb").read())
    q = int(b[2:7], 2)
    ds = dcscaler(q)
    defv = 1024 // ds
    mbw, mbh = W // 16, H // 16
    sig = R.sig_end(b)
    p = 17
    cw, ch = mbw * 16, mbh * 16
    Y = np.full((ch, cw), 128.0)
    Cbp = np.full((ch // 2, cw // 2), 128.0)
    Crp = np.full((ch // 2, cw // 2), 128.0)
    gCb = np.full((mbh, mbw), defv)
    gCr = np.full((mbh, mbw), defv)
    acrowC = {4: np.zeros((mbh, mbw, 8)), 5: np.zeros((mbh, mbw, 8))}
    accolC = {4: np.zeros((mbh, mbw, 8)), 5: np.zeros((mbh, mbw, 8))}
    # grids: DC levels per block; coded flags; AC first row/col (quantized)
    dcL = np.full((2 * mbh, 2 * mbw), defv)
    codedL = np.zeros((2 * mbh, 2 * mbw), int)
    acrow = np.zeros((2 * mbh, 2 * mbw, 8))
    accol = np.zeros((2 * mbh, 2 * mbw, 8))
    o = open(oraclef, "rb").read()
    Yo = np.frombuffer(o[: W * H], np.uint8).reshape(H, W).astype(float)
    Cbo = (
        np.frombuffer(o[W * H : W * H + W * H // 4], np.uint8)
        .reshape(H // 2, W // 2)
        .astype(float)
    )
    Cro = (
        np.frombuffer(o[W * H + W * H // 4 : W * H + W * H // 2], np.uint8)
        .reshape(H // 2, W // 2)
        .astype(float)
    )
    mbmse = []
    for my in range(mbh):
        for mx in range(mbw):
            m = None
            for L in range(1, 14):
                if b[p : p + L] in mb_intra:
                    m = b[p : p + L]
                    break
            if m is None:
                return f"MBfail@{p} MB({mx},{my})", mbmse
            cbpk = mb_intra[m]
            p += len(m)
            cbcr, cbpy = cbpk.split("_")
            raw = [int(cbpy[i]) for i in range(4)] + [int(cbcr[0]), int(cbcr[1])]
            cbp = [0] * 6
            dirs = [0] * 4
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = codedL[by][bx - 1] if bx > 0 else 0
                Bb = codedL[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                Cc = codedL[by - 1][bx] if by > 0 else 0
                cbp[i] = raw[i] ^ (A if Bb == Cc else Cc)
                codedL[by][bx] = cbp[i]  # note: pred uses A/B/C coded
            cbp[4] = raw[4]
            cbp[5] = raw[5]
            acpred = b[p]
            p += 1
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                diff, n = dcdec(b, p, tab)
                if diff is None:
                    return f"DCfail@{p} MB({mx},{my})blk{blk}", mbmse
                p += n
                if blk < 4:
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                else:
                    bx, by = mx, my
                # DC gradient prediction (luma grid; chroma simplified)
                if blk < 4:
                    a = dcL[by][bx - 1] if bx > 0 else defv
                    bb = dcL[by - 1][bx - 1] if (bx > 0 and by > 0) else defv
                    c = dcL[by - 1][bx] if by > 0 else defv
                    fromleft = abs(a - bb) > abs(bb - c)
                    # MS quirk: acpred=1 top-row blocks use avg(topleft, gradient-neighbour)
                    if acpred == "1" and blk in (0, 1) and bx > 0 and by > 0:
                        pred = (a + bb) // 2 if fromleft else (bb + c) // 2
                    else:
                        pred = a if fromleft else c
                else:
                    g = gCb if blk == 4 else gCr
                    a = g[my][mx - 1] if mx > 0 else defv
                    bb = g[my - 1][mx - 1] if (mx > 0 and my > 0) else defv
                    c = g[my - 1][mx] if my > 0 else defv
                    fromleft = abs(a - bb) > abs(bb - c)
                    # MS quirk: acpred=1 top-row blocks use avg(topleft, gradient-neighbour)
                    if acpred == "1" and blk in (0, 1) and bx > 0 and by > 0:
                        pred = (a + bb) // 2 if fromleft else (bb + c) // 2
                    else:
                        pred = a if fromleft else c
                lev = pred + diff
                if blk >= 4:
                    (gCb if blk == 4 else gCr)[my][mx] = lev
                if blk < 4:
                    dcL[by][bx] = lev
                qf = np.zeros(64)
                qf[0] = lev
                if cbp[blk]:
                    scan = zigzag
                    if acpred == "1":
                        scan = alt_v if fromleft else alt_h
                    pos = 1
                    while True:
                        dt = dec_tcoef(b, p, blk >= 4)
                        if dt is None:
                            return (
                                f"ACfail@{p} MB({mx},{my})blk{blk}: {b[p:p+18]}",
                                mbmse,
                            )
                        (run, level, last), ln = dt
                        p += ln
                        pos += run
                        if pos < 64:
                            qf[scan[pos]] = level
                        pos += 1
                        if last:
                            break
                # AC prediction — applies for ap=1 regardless of cbp (predicted, not coded)
                quirk = acpred == "1" and blk in (0, 1) and bx > 0 and by > 0
                if acpred == "1" and blk < 4:
                    if quirk:
                        # MS quirk: AC prediction = avg(topleft, gradient-neighbour)
                        if fromleft:
                            for j in range(1, 8):
                                qf[j * 8] += (
                                    accol[by][bx - 1][j] + accol[by - 1][bx - 1][j]
                                ) // 2
                        else:
                            for i in range(1, 8):
                                qf[i] += (
                                    acrow[by - 1][bx][i] + acrow[by - 1][bx - 1][i]
                                ) // 2
                    elif fromleft and bx > 0:
                        for j in range(1, 8):
                            qf[j * 8] += accol[by][bx - 1][j]
                    elif (not fromleft) and by > 0:
                        for i in range(1, 8):
                            qf[i] += acrow[by - 1][bx][i]
                if acpred == "1" and blk >= 4 and cbp[blk]:
                    ar, ac = acrowC[blk], accolC[blk]
                    if fromleft and mx > 0:
                        for j in range(1, 8):
                            qf[j * 8] += ac[my][mx - 1][j]
                    elif (not fromleft) and my > 0:
                        for i in range(1, 8):
                            qf[i] += ar[my - 1][mx][i]
                if blk < 4:
                    for i in range(1, 8):
                        acrow[by][bx][i] = qf[i]
                        accol[by][bx][i] = qf[i * 8]
                else:
                    for i in range(1, 8):
                        acrowC[blk][my][mx][i] = qf[i]
                        accolC[blk][my][mx][i] = qf[i * 8]
                # dequant + idct
                F = np.zeros((8, 8))
                F[0][0] = qf[0] * ds
                for i in range(1, 64):
                    if qf[i]:
                        F[i // 8][i % 8] = dequant_ac(int(qf[i]), q)
                px = idct(F)
                if blk < 4:
                    r0, c0 = (2 * my + blk // 2) * 8, (2 * mx + blk % 2) * 8
                    Y[r0 : r0 + 8, c0 : c0 + 8] = np.clip(px, 0, 255)
                elif blk == 4:
                    Cbp[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8] = np.clip(px, 0, 255)
                else:
                    Crp[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8] = np.clip(px, 0, 255)
            # per-MB luma+chroma MSE
            r0, c0 = my * 16, mx * 16
            if r0 + 16 <= H and c0 + 16 <= W:
                d = Y[r0 : r0 + 16, c0 : c0 + 16] - Yo[r0 : r0 + 16, c0 : c0 + 16]
                cb = (
                    Cbp[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8]
                    - Cbo[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8]
                )
                cr = (
                    Crp[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8]
                    - Cro[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8]
                )
                mbmse.append(
                    (
                        mx,
                        my,
                        round((d * d).mean(), 1),
                        round((cb * cb).mean(), 1),
                        round((cr * cr).mean(), 1),
                    )
                )
            if p >= sig:
                break
        else:
            continue
        break
    return "ok", mbmse


st, mse = decode("/tmp/divx/cfg0.bin", 512, 288, "/tmp/divx/cfg0_true.yuv")
print("status:", st)
print("per-MB MSE (first 12):", [m[2] for m in mse[:12]])
badL = [m for m in mse if m[2] > 5]
badC = [m for m in mse if len(m) > 3 and (m[3] > 5 or m[4] > 5)]
print(f"first LUMA-bad: {badL[0] if badL else None}")
print(f"first CHROMA-bad: {badC[0] if badC else None}")
print(
    f'overall MSE (decoded MBs): {round(np.mean([m[2] for m in mse]),2) if mse else "n/a"}'
)
