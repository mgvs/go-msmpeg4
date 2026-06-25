"""Bigger controlled ap=1 DC-quirk collection: 192x192 grayscale, varied H/V gradients
(both fromleft directions), chroma-flat guard + DC-align guard. Rich per-point:
(blk, oracle_DC o, read diff, left/top/topleft oracle DCs). Dump to /tmp/dcbig.pkl."""

import subprocess, numpy as np, json, sys, pickle

sys.path.insert(0, ".")
import divx_encode as DE, extract_div3 as EX

mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
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


def dcdec(b, p, tab):
    for n in range(1, 36):
        if b[p : p + n] in tab:
            return tab[b[p : p + n]], n
    return None, 0


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


def collect(frame, Yt, Cb, Cr, q4, W, H):
    b = "".join(format(x, "08b") for x in frame)
    mbw, mbh = W // 16, H // 16

    def odc(bx, by):
        return round(
            (Mm @ Yt[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T)[0][0] / 8
        )

    def cflat(mx, my):
        for pl in (Cb, Cr):
            C = Mm @ pl[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8] @ Mm.T
            if any(abs(C[u][v]) >= q4 for u in range(8) for v in range(8) if (u or v)):
                return False
        return True

    dcL = np.full((2 * mbh, 2 * mbw), -999)
    p = 17
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
            cbp = [int(cbpy[i]) for i in range(4)] + [
                0,
                0,
            ]  # chroma forced 0 (grayscale)
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
                    o = odc(bx, by)
                    L = dcL[by][bx - 1] if bx > 0 else -999
                    T = dcL[by - 1][bx] if by > 0 else -999
                    TL = dcL[by - 1][bx - 1] if (bx > 0 and by > 0) else -999
                    if L != -999 and T != -999 and TL != -999:
                        pred = o - diff
                        if min(abs(pred - L), abs(pred - T), abs(pred - TL)) > 50:
                            return out  # drift guard
                        if blk in (0, 1) and ap == "1":
                            out.append(
                                dict(
                                    blk=blk,
                                    o=o,
                                    diff=diff,
                                    left=int(L),
                                    top=int(T),
                                    topleft=int(TL),
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


W = H = 192
allpts = []
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
# varied gradients: horizontal, vertical, diagonal, mixed -> both fromleft directions
pats = [
    lambda i, j, t: 90 + i * 0.4 + j * 1.4 + t * 3,
    lambda i, j, t: 90 + i * 1.4 + j * 0.4 + t * 3,
    lambda i, j, t: 100 + i * 1.0 + j * 1.0 + t * 3,
    lambda i, j, t: 128 + 40 * np.sin(i / 15 + t) + 10 * j * 0.0,
    lambda i, j, t: 128 + 40 * np.sin(j / 15 + t),
    lambda i, j, t: 100 + i * 1.2 + 15 * np.cos(j / 12 + t),
    lambda i, j, t: 100 + j * 1.2 + 15 * np.cos(i / 12 + t),
]
for t in range(N):
    Y = np.fromfunction(lambda i, j: pats[t % len(pats)](i, j, t), (H, W)).astype(
        np.uint8
    )
    frame = DE.encode(Y.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes(), W, H)
    if frame is None or EX.config(frame)[2] != 2:
        print(f"f{t}:skip")
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
    pts = collect(frame, Yt, Cb, Cr, EX.config(frame)[0], W, H)
    allpts += pts
    print(f"f{t}: +{len(pts)} (total {len(allpts)})")
pickle.dump(allpts, open("/tmp/dcbig.pkl", "wb"))
print(f"\nTOTAL: {len(allpts)} ap=1 top-row DC points -> /tmp/dcbig.pkl")
