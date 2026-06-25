"""Collect coded-AC + final-AC + neighbour rows/cols for the 4 acpred=1 top-row quirk
blocks to reverse the MS AC-prediction rule. Decode each frame, at a quirk block dump
the coded coefficients (my parse) and compare to the oracle final + bb/c rows/cols."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

mbi = json.load(open("data/table_mb_intra_raw.json"))
mb_intra = {v: k for k, v in mbi.items()}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
rlc = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_chroma.json")).items()
}
c2l = {v: k for k, v in rl.items()}
c2c = {v: k for k, v in rlc.items()}
mxl = {}
mxc = {}
for r, l, la in rl:
    mxl[(la, r)] = max(mxl.get((la, r), 0), l)
for r, l, la in rlc:
    mxc[(la, r)] = max(mxc.get((la, r), 0), l)
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


def dect(b, p, ch=False):
    cm = c2c if ch else c2l
    ml = mxc if ch else mxl
    esc = "1011010" if ch else "0000011"
    if b[p : p + 7] == esc:
        q = p + 7
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
        for L in range(1, 17):
            if b[q : q + L] in cm:
                rl_ = cm[b[q : q + L]]
                break
        else:
            return None
        run, lev, last = rl_
        q += L
        sign = b[q]
        q += 1
        if m == 1:
            lev += ml.get((last, run), 0)
        return (run, -lev if sign == "1" else lev, last), q - p
    for L in range(1, 17):
        if b[p : p + L] in cm:
            r = cm[b[p : p + L]]
            return (r[0], -r[1] if b[p + L] == "1" else r[1], r[2]), L + 1
    return None


def dcdec(b, p, tab):
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in tab:
            return tab[c], n


def dcscaler(q):
    return 8 if q <= 4 else (2 * q if q <= 8 else (q + 8 if q <= 24 else 2 * q - 16))


def collect(binf, oyuv, quirks):
    b = "".join(format(x, "08b") for x in open(binf, "rb").read())
    W, H = 512, 288
    q = int(b[2:7], 2)
    ds = dcscaler(q)
    defv = 1024 // ds
    mbw, mbh = W // 16, H // 16
    Yt = np.frombuffer(oyuv[: W * H], np.uint8).reshape(H, W).astype(float)

    def ocoef(bx, by):
        C = Mm @ Yt[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T

        def L(F):
            return 0 if abs(F) < q else int(np.sign(F) * round((abs(F) / q - 1) / 2))

        return {(u, v): L(C[u][v]) for u in range(8) for v in range(8)}

    p = 17
    codedL = np.zeros((2 * mbh, 2 * mbw), int)
    out = []
    for my in range(mbh):
        for mx in range(mbw):
            m = None
            for Ln in range(1, 14):
                if b[p : p + Ln] in mb_intra:
                    m = b[p : p + Ln]
                    break
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
            ap = b[p]
            p += 1
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                diff, n = dcdec(b, p, tab)
                p += n
                coded = {}
                if cbp[blk]:
                    pos = 1
                    while True:
                        r = dect(b, p, blk >= 4)
                        if r is None:
                            return out
                        (run, lev, last), ln = r
                        p += ln
                        pos += run
                        scan = alt_h
                        if pos < 64:
                            coded[(scan[pos] // 8, scan[pos] % 8)] = lev
                        pos += 1
                        if last:
                            break
                if blk < 4 and blk in (0, 1) and ap == "1":
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                    if bx > 0 and by > 0:
                        fin = ocoef(bx, by)
                        bbb = ocoef(bx - 1, by - 1)
                        cc = ocoef(bx, by - 1)
                        aa = ocoef(bx - 1, by)
                        out.append(
                            dict(
                                f=binf,
                                mx=mx,
                                my=my,
                                blk=blk,
                                cbp=cbp[blk],
                                coded={k: v for k, v in coded.items() if v},
                                final={
                                    k: v for k, v in fin.items() if v and k != (0, 0)
                                },
                                bb_full={
                                    k: v for k, v in bbb.items() if v and k != (0, 0)
                                },
                                c_full={
                                    k: v for k, v in cc.items() if v and k != (0, 0)
                                },
                                a_full={
                                    k: v for k, v in aa.items() if v and k != (0, 0)
                                },
                            )
                        )
    return out


# decode to find quirk blocks first (acpred=1 top-row select-failures) -> use known from collect_dc
# known quirks: 5.bin MB(3,1)b0,MB(4,1)b0; 3.bin MB(1,1)b0,b1
import os

allpts = []
for f in ["5", "6", "7", "1", "2", "3"]:
    qb = set()
    fb = open(f"/tmp/divx/{f}.bin", "rb").read()
    sk = open("/tmp/sk_frame.bin", "rb").read()
    skavi = bytearray(open("/tmp/sk.avi", "rb").read())
    skoff = skavi.find(sk)
    a = bytearray(skavi)
    a[skoff : skoff + len(sk)] = fb + sk[len(fb) :]
    open("/tmp/o.avi", "wb").write(a)
    oy = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/o.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    try:
        allpts += collect(f"/tmp/divx/{f}.bin", oy, qb)
    except Exception as e:
        print(f"{f}: {e}")

import json as _j

cb0 = [d for d in allpts if d["cbp"] == 0 and d["final"]]
print(f"cbp=0 blocks with AC: {len(cb0)}")


def colvals(full):
    return {u: full.get((u, 0), 0) for u in range(1, 8)}


def rowvals(full):
    return {v: full.get((0, v), 0) for v in range(1, 8)}


def finc(final):
    return {u: final.get((u, 0), 0) for u in range(1, 8)}


def finr(final):
    return {v: final.get((0, v), 0) for v in range(1, 8)}


# score: how many cbp=0 blocks have final-col == source-col (exact) and final-row==source-row
from collections import Counter

score = Counter()
for d in cb0:
    fc = finc(d["final"])
    fr = finr(d["final"])
    for nm, full in [("bb", d["bb_full"]), ("c", d["c_full"]), ("a", d["a_full"])]:
        if fc == colvals(full):
            score[nm + "-col"] += 1
        if fr == rowvals(full):
            score[nm + "-row"] += 1
        # scaled by q
        if fc == {u: colvals(full)[u] * 4 for u in range(1, 8)}:
            score[nm + "-col*4"] += 1
print("exact-match counts (of %d):" % len(cb0))
for k, v in score.most_common():
    print(f"  {k}: {v}")
# focus: blocks where final is ONLY column vs ONLY row
onlycol = [d for d in cb0 if all(k[1] == 0 for k in d["final"])]
onlyrow = [d for d in cb0 if all(k[0] == 0 for k in d["final"])]
print(
    f"\nfinal ONLY-column: {len(onlycol)}, ONLY-row: {len(onlyrow)}, mixed: {len(cb0)-len(onlycol)-len(onlyrow)}"
)
print("\nsample only-column blocks (final col vs a/bb/c col):")
for d in onlycol[:8]:
    print(
        f"  {d['f']} MB({d['mx']},{d['my']})b{d['blk']}: fin-col={finc(d['final'])} | a-col={colvals(d['a_full'])} bb-col={colvals(d['bb_full'])} c-col={colvals(d['c_full'])}"
    )
