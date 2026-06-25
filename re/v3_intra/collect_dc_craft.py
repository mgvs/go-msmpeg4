"""Controlled DC-quirk reversal: grayscale frames -> DivX3 -> ffmpeg oracle.
For each acpred=1 top-row luma block (blk0/blk1, non-edge), record oracle DC, the read
DC diff, and neighbour DCs (A=topleft, B=top, C=left). Alignment guard: predictor =
oracleDC - diff must be plausible (near neighbours) else parser drifted -> stop frame.
Then: does predictor = SELECT (spec) or AVG(topleft,gradient) (MS quirk)? Decisive on
clean controlled data with KNOWN content."""

import subprocess, numpy as np, json, sys, pickle

sys.path.insert(0, ".")
import divx_encode as DE, extract_div3 as EX

mbi = json.load(open("data/table_mb_intra_raw.json"))
mb_intra = {v: k for k, v in mbi.items()}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
c2l = {v: k for k, v in rl.items()}
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


def dect(b, p):
    if b[p : p + 7] == "0000011":
        q = p + 7
        if b[q] == "1":
            q += 1
        elif b[q : q + 2] == "01":
            q += 2
        else:
            return int(b[q + 2]), (q + 2 + 1 + 6 + 8) - p
        for L in range(1, 17):
            if b[q : q + L] in c2l:
                return c2l[b[q : q + L]][2], (q + L + 1) - p
        return None, 0
    for L in range(1, 17):
        if b[p : p + L] in c2l:
            return c2l[b[p : p + L]][2], L + 1
    return None, 0


def dcdec(b, p, tab):
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in tab:
            return tab[c], n
    return None, 0


def dcscale(q):
    return 8 if q <= 4 else (2 * q if q <= 8 else (q + 8 if q <= 24 else 2 * q - 16))


def collect(frame, W, H, Yt, Cb, Cr, q):
    b = "".join(format(x, "08b") for x in frame)
    mbw, mbh = W // 16, H // 16
    ds = dcscale(q)

    def odc(pl, bx, by):
        return round(
            (Mm @ pl[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T)[0][0] / 8
        )

    def cflat(mx, my):
        for pl in (Cb, Cr):
            C = Mm @ pl[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8] @ Mm.T
            if any(abs(C[u][v]) >= q for u in range(8) for v in range(8) if (u or v)):
                return False
        return True

    dcL = np.full((2 * mbh, 2 * mbw), -999)
    p = 17
    codedL = np.zeros((2 * mbh, 2 * mbw), int)
    out = []
    for my in range(mbh):
        for mx in range(mbw):
            if not cflat(mx, my):
                return out
            m = None
            for Ln in range(1, 14):
                if b[p : p + Ln] in mb_intra:
                    m = b[p : p + Ln]
                    break
            if m is None:
                return out
            cbpk = mb_intra[m]
            p += len(m)
            cbcr, cbpy = cbpk.split("_")
            raw = [int(cbpy[i]) for i in range(4)]
            cbp = [0] * 6
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = codedL[by][bx - 1] if bx > 0 else 0
                Bb = codedL[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                Cc = codedL[by - 1][bx] if by > 0 else 0
                cbp[i] = raw[i] ^ (A if Bb == Cc else Cc)
                codedL[by][bx] = cbp[i]
            ap = b[p]
            p += 1
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                diff, n = dcdec(b, p, tab)
                if diff is None:
                    return out
                p += n
                if blk < 4:
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                    o = odc(Yt, bx, by)
                    pred_actual = (
                        o - diff
                    )  # predictor (same domain as lev=pred+diff, no ds)
                    A = dcL[by][bx - 1] if bx > 0 else -999
                    B = dcL[by - 1][bx] if by > 0 else -999
                    Cc = dcL[by - 1][bx - 1] if (bx > 0 and by > 0) else -999
                    # alignment guard: predictor must be near a neighbour (within 64*ds? in DC units ~ within 40)
                    neigh = [x for x in (A, B, Cc) if x != -999]
                    if neigh and min(abs(pred_actual - nv) for nv in neigh) > 60:
                        return out  # drifted
                    if (
                        blk in (0, 1)
                        and ap == "1"
                        and bx > 0
                        and by > 0
                        and A != -999
                        and B != -999
                        and Cc != -999
                    ):
                        # candidates (DC units): SELECT = (|A-Cc|<=|A-B|)?B:Cc ; AVG variants
                        sel = (
                            B if abs(Cc - A) <= abs(Cc - B) else A
                        )  # A=topleft? define: topleft=Cc? careful
                        out.append(
                            dict(
                                blk=blk,
                                o=o,
                                diff=diff,
                                ds=ds,
                                topleft=Cc,
                                top=B,
                                left=A,
                            )
                        )
                    dcL[by][bx] = o
                if blk < 4 and cbp[blk]:
                    while True:
                        last, ln = dect(b, p)
                        if last is None:
                            return out
                        p += ln
                        if last:
                            break
    return out


W = H = 128
allpts = []
pats = [
    lambda i, j, t: 90 + i * 1.4 + j * 0.4 + t * 6,
    lambda i, j, t: 128 + 50 * np.sin((i + t * 4) / 10),
    lambda i, j, t: 100 + i * 1.6 + 15 * np.cos(j / 7 + t),
    lambda i, j, t: 110 + i * 0.3 + j * 1.5 + t * 4,
    lambda i, j, t: 128 + 55 * np.sin(i / 8) * np.sin(j / 8 + t),
]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
for t in range(N):
    Y = np.fromfunction(lambda i, j: pats[t % len(pats)](i, j, t), (H, W)).astype(
        np.uint8
    )
    frame = DE.encode(Y.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes(), W, H)
    if frame is None or EX.config(frame)[2] != 2:
        print(f"f{t}: skip")
        continue
    oy = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/de_out.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    if len(oy) < W * H * 3 // 2:
        continue
    Yt = np.frombuffer(oy[: W * H], np.uint8).reshape(H, W).astype(float)
    Cb = (
        np.frombuffer(oy[W * H : W * H + W * H // 4], np.uint8)
        .reshape(H // 2, W // 2)
        .astype(float)
    )
    Cr = (
        np.frombuffer(oy[W * H + W * H // 4 :], np.uint8)
        .reshape(H // 2, W // 2)
        .astype(float)
    )
    pts = collect(frame, W, H, Yt, Cb, Cr, EX.config(frame)[0])
    allpts += pts
    print(f"f{t}: +{len(pts)} (total {len(allpts)})")
pickle.dump(allpts, open("/tmp/dcpts.pkl", "wb"))
# analyze
print(f"\nTOTAL acpred=1 top-row DC points: {len(allpts)}")
nsel = navg = both = neither = 0
for d in allpts:
    left = d["left"]
    top = d["top"]
    topleft = d["topleft"]
    o = d["o"]
    diff = d["diff"]
    pred = o - diff
    fromleft = abs(left - topleft) > abs(topleft - top)
    select = left if fromleft else top
    avg = (left + topleft) // 2 if fromleft else (topleft + top) // 2
    s_ok = abs(pred - select) <= 1
    a_ok = abs(pred - avg) <= 1
    nsel += s_ok
    navg += a_ok
    if s_ok and a_ok:
        both += 1
    elif not s_ok and not a_ok:
        neither += 1
n = len(allpts)
print(f"predictor == SELECT(spec):           {nsel}/{n}")
print(f"predictor == AVG(topleft,grad)quirk: {navg}/{n}")
print(f"both match (ambiguous): {both}, neither: {neither}")
# decisive: cases where they DIFFER
diffcases = [
    d
    for d in allpts
    if (
        lambda left, top, topleft: abs(
            (left if abs(left - topleft) > abs(topleft - top) else top)
            - (
                (left + topleft) // 2
                if abs(left - topleft) > abs(topleft - top)
                else (topleft + top) // 2
            )
        )
        > 1
    )(d["left"], d["top"], d["topleft"])
]
ds_sel = ds_avg = 0
for d in diffcases:
    left = d["left"]
    top = d["top"]
    topleft = d["topleft"]
    pred = d["o"] - d["diff"]
    fromleft = abs(left - topleft) > abs(topleft - top)
    select = left if fromleft else top
    avg = (left + topleft) // 2 if fromleft else (topleft + top) // 2
    if abs(pred - select) <= 1:
        ds_sel += 1
    if abs(pred - avg) <= 1:
        ds_avg += 1
print(
    f"\nDECISIVE (select!=avg, {len(diffcases)} cases): SELECT={ds_sel}, AVG={ds_avg}"
)
