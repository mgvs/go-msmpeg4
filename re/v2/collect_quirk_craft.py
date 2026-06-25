"""Controlled crafting: grayscale frames -> DivX3(VirtualDub) -> ffmpeg oracle ->
collect acpred=1 cbp=0 luma quirk blocks (final=pure prediction) + neighbours.
Grayscale => chroma provably flat => force chroma cbp=0; guard stops the frame if the
oracle chroma block is NOT flat (real chroma coding => our force would misalign).
No cascade, unlimited fresh frames. Pin the AC-prediction quirk with KNOWN content."""

import subprocess, numpy as np, json, sys, os, pickle

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


def collect(frame, W, H, Yt, Cb, Cr, q):
    b = "".join(format(x, "08b") for x in frame)
    mbw, mbh = W // 16, H // 16

    def coef(pl, bx, by):
        C = Mm @ pl[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T

        def L(F):
            return 0 if abs(F) < q else int(np.sign(F) * round((abs(F) / q - 1) / 2))

        return {(u, v): L(C[u][v]) for u in range(8) for v in range(8)}

    def chroma_flat(mx, my):  # both Cb,Cr blocks of this MB flat (no AC)?
        for pl in (Cb, Cr):
            C = Mm @ pl[my * 8 : my * 8 + 8, mx * 8 : mx * 8 + 8] @ Mm.T
            if any(abs(C[u][v]) >= q for u in range(8) for v in range(8) if (u or v)):
                return False
        return True

    p = 17
    codedL = np.zeros((2 * mbh, 2 * mbw), int)
    out = []
    for my in range(mbh):
        for mx in range(mbw):
            if not chroma_flat(mx, my):
                return out  # real chroma coding -> stop (force invalid)
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
                if blk in (0, 1) and ap == "1" and cbp[blk] == 0:
                    bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                    if bx > 0 and by > 0:
                        fin = coef(Yt, bx, by)
                        bb = coef(Yt, bx - 1, by - 1)
                        cc = coef(Yt, bx, by - 1)
                        aa = coef(Yt, bx - 1, by)
                        out.append(
                            dict(
                                blk=blk,
                                F={k: v for k, v in fin.items() if v and k != (0, 0)},
                                bb={k: v for k, v in bb.items() if v and k != (0, 0)},
                                c={k: v for k, v in cc.items() if v and k != (0, 0)},
                                a={k: v for k, v in aa.items() if v and k != (0, 0)},
                                aDC=aa[(0, 0)],
                                bbDC=bb[(0, 0)],
                                cDC=cc[(0, 0)],
                            )
                        )
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
patterns = [
    lambda i, j, t: 90 + i * 1.1 + j * 0.6 + t * 7,
    lambda i, j, t: 128 + 45 * np.sin((i + t * 5) / 13) * np.cos(j / 11),
    lambda i, j, t: 100 + i * 0.5 + j * 1.3 + 20 * np.sin(i / 9 + t),
    lambda i, j, t: 110 + 30 * np.sin(i / 7 + t) + 25 * np.cos(j / 8),
    lambda i, j, t: 128 + 50 * np.cos((i * j) / 300 + t),
    lambda i, j, t: 80 + i * 0.9 + 40 * np.sin(j / 6 + t),
]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
for t in range(N):
    Y = np.fromfunction(
        lambda i, j: patterns[t % len(patterns)](i, j, t), (H, W)
    ).astype(np.uint8)
    frame = DE.encode(Y.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes(), W, H)
    if frame is None:
        print(f"f{t}: encode FAIL")
        continue
    cfg = EX.config(frame)
    if cfg[2] != 2:
        print(f"f{t}: cfg={cfg} skip (not rlt=2)")
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
        print(f"f{t}: oracle FAIL")
        continue
    Yt = np.frombuffer(oy[: W * H], np.uint8).reshape(H, W).astype(float)
    Cb = (
        np.frombuffer(oy[W * H : W * H + W * H // 4], np.uint8)
        .reshape(H // 2, W // 2)
        .astype(float)
    )
    Cr = (
        np.frombuffer(oy[W * H + W * H // 4 : W * H * 3 // 2], np.uint8)
        .reshape(H // 2, W // 2)
        .astype(float)
    )
    pts = collect(frame, W, H, Yt, Cb, Cr, cfg[0])
    allpts += pts
    print(f"f{t}: cfg={cfg} +{len(pts)} (total {len(allpts)})")
pickle.dump(allpts, open("/tmp/craftpts.pkl", "wb"))
print(f"TOTAL clean controlled quirk points: {len(allpts)} -> /tmp/craftpts.pkl")
