"""Collect the first DC-prediction divergence point from each config-0 keyframe to
reverse the MS DC predictor formula. truepred = oracle_lev - decoded_diff."""

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
code2rl = {v: k for k, v in rl.items()}
code2rlc = {v: k for k, v in rlc.items()}
maxlev = {}
maxlevc = {}
for r, l, la in rl:
    maxlev[(la, r)] = max(maxlev.get((la, r), 0), l)
for r, l, la in rlc:
    maxlevc[(la, r)] = max(maxlevc.get((la, r), 0), l)
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
skf = open("/tmp/sk_frame.bin", "rb").read()
skavi = bytearray(open("/tmp/sk.avi", "rb").read())
skoff = skavi.find(skf)


def oracle(binfn):
    fb = open(binfn, "rb").read()
    if len(fb) > len(skf):
        return None
    a = bytearray(skavi)
    a[skoff : skoff + len(skf)] = fb + skf[len(fb) :]
    open("/tmp/orc.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/orc.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    return o[: 512 * 288 * 3 // 2] if len(o) >= 512 * 288 * 3 // 2 else None


def dcdec(b, p, tab):
    c = ""
    n = 0
    while True:
        c += b[p + n]
        n += 1
        if c in tab:
            return tab[c], n


def dect(b, p, chroma):
    cm = code2rlc if chroma else code2rl
    ml = maxlevc if chroma else maxlev
    esc = "1011010" if chroma else "0000011"
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
        q += L + 1
        if m == 1:
            lev += ml.get((last, run), 0)
        return (run, lev, last), q - p
    for L in range(1, 17):
        if b[p : p + L] in cm:
            return (
                cm[b[p : p + L]][0],
                cm[b[p : p + L]][1],
                cm[b[p : p + L]][2],
            ), L + 1
    return None


def dcscaler(q):
    return 8 if q <= 4 else (2 * q if q <= 8 else (q + 8 if q <= 24 else 2 * q - 16))


def first_div(binfn, oyuv):
    global PTS
    b = "".join(format(x, "08b") for x in open(binfn, "rb").read())
    W, H = 512, 288
    q = int(b[2:7], 2)
    ds = dcscaler(q)
    defv = 1024 // ds
    Yt = np.frombuffer(oyuv[: W * H], np.uint8).reshape(H, W).astype(float)
    mbw, mbh = W // 16, H // 16
    ODC = np.zeros((2 * mbh, 2 * mbw))
    for by in range(2 * mbh):
        for bx in range(2 * mbw):
            ODC[by][bx] = round(
                (Mm @ Yt[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T)[0][0] / ds
            )
    p = 17
    codedL = np.zeros((2 * mbh, 2 * mbw), int)
    dcL = np.full((2 * mbh, 2 * mbw), defv)
    for my in range(mbh):
        for mx in range(mbw):
            m = None
            for L in range(1, 14):
                if b[p : p + L] in mb_intra:
                    m = b[p : p + L]
                    break
            if m is None:
                return None
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
            p += 1
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                diff, n = dcdec(b, p, tab)
                p += n
                if blk < 4:
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                    a = dcL[by][bx - 1] if bx > 0 else defv
                    bb = dcL[by - 1][bx - 1] if (bx > 0 and by > 0) else defv
                    c = dcL[by - 1][bx] if by > 0 else defv
                    fromleft = abs(a - bb) > abs(bb - c)
                    predsel = a if fromleft else c
                    truelev = int(ODC[by][bx])
                    if (
                        predsel != (truelev - diff) and bx > 0 and by > 0
                    ):  # non-edge divergence
                        PTS.append(
                            (
                                int(a),
                                int(bb),
                                int(c),
                                bool(fromleft),
                                truelev - diff,
                                blk,
                            )
                        )
                    dcL[by][bx] = truelev  # TRUE value keeps grid+consumption aligned
                if cbp[blk]:
                    while True:
                        r = dect(b, p, blk >= 4)
                        if r is None:
                            return None
                        (run, level, last), ln = r
                        p += ln
                        if last:
                            break
    return None


PTS = []
pts = []
allpts = []
for f in ["5", "6", "7", "1", "2", "3", "21"]:
    oy = oracle(f"/tmp/divx/{f}.bin")
    if oy is None:
        print(f"{f}: oracle failed (too big)")
        continue
    PTS = []
    first_div(f"/tmp/divx/{f}.bin", oy)
    print(f"{f}.bin: {len(PTS)} non-edge divergence points")
    allpts += [(a, bb, c, fl, tp, blk, f) for (a, bb, c, fl, tp, blk) in PTS]
json.dump(allpts, open("data/dc_points.json", "w"))
print(f"\nTOTAL: {len(allpts)} points")
# analyze: how many match avg(bb,c)[from-c] / avg(a,bb)[from-a]?
import collections

fc = [(a, bb, c, tp) for (a, bb, c, fl, tp, blk, f) in allpts if not fl]
fa = [(a, bb, c, tp) for (a, bb, c, fl, tp, blk, f) in allpts if fl]
print(f"from-c: {len(fc)}, from-a: {len(fa)}")
print(
    f"  from-c avg(bb,c) match: {sum(1 for a,bb,c,tp in fc if (bb+c)//2==tp)}/{len(fc)}"
)
print(f"  from-c planar match: {sum(1 for a,bb,c,tp in fc if a+c-bb==tp)}/{len(fc)}")
print(
    f"  from-a avg(a,bb) match: {sum(1 for a,bb,c,tp in fa if (a+bb)//2==tp)}/{len(fa)}"
)
print(f"  from-a planar match: {sum(1 for a,bb,c,tp in fa if a+c-bb==tp)}/{len(fa)}")
print("sample from-c pts (a,bb,c->tp):", [(a, bb, c, tp) for a, bb, c, tp in fc[:12]])
