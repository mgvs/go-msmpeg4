"""Comprehensive RL table reverser via DivX3 single-coef common-prefix.
mode last1: each block = DC + one coef at scan run R -> code(R,level,last=1).
mode last0: each block = coef at R + anchor at R+1 -> target code(R,level,last=0);
            2nd pos/neg diff = anchor sign -> stays aligned.
Config targeted by frame SIZE (64->rlt0, 128->rlt2, ... probe for rlt1). Accumulate per
config (rlc,rlt). Usage: python3 reverse_tables.py <mode last0|last1> <W> <Nframes>"""

import subprocess, numpy as np, json, sys, pickle, collections, os

sys.path.insert(0, ".")
import divx_encode as DE, extract_div3 as EX

mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
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


def basis(u, v):
    B = np.zeros((8, 8))
    B[u, v] = 1
    return Mm.T @ B @ Mm


ZZ = [
    (0, 0),
    (0, 1),
    (1, 0),
    (2, 0),
    (1, 1),
    (0, 2),
    (0, 3),
    (1, 2),
    (2, 1),
    (3, 0),
    (4, 0),
    (3, 1),
    (2, 2),
    (1, 3),
    (0, 4),
    (0, 5),
    (1, 4),
    (2, 3),
    (3, 2),
    (4, 1),
    (5, 0),
    (6, 0),
    (5, 1),
    (4, 2),
    (3, 3),
    (2, 4),
    (1, 5),
    (0, 6),
    (0, 7),
    (1, 6),
]


def dclen(b, p, tab):
    for n in range(1, 36):
        if b[p : p + n] in tab:
            return n
    return 0


def lvl(C, u, v, q):
    a = abs(C[u, v])
    return int(round((a / q - 1) / 2)) if a >= q else 0


MODE = sys.argv[1] if len(sys.argv) > 1 else "last1"
W = H = int(sys.argv[2]) if len(sys.argv) > 2 else 64
N = int(sys.argv[3]) if len(sys.argv) > 3 else 16
OUT = f"/tmp/rl_{MODE}_{W}.pkl"
tables = collections.defaultdict(dict)
if os.path.exists(OUT):
    for k, v in pickle.load(open(OUT, "rb")).items():
        kk = eval(k)
        tables[kk] = {tuple(int(x) for x in kk2.split(",")): c for kk2, c in v.items()}


def mkY(run_fn, amp, sign):
    Y = np.full((H, W), 128.0)
    ncol = W // 8
    for by in range(0, H, 8):
        for bx in range(0, W, 8):
            R = run_fn(bx // 8, by // 8)
            u, v = ZZ[R]
            blk = sign * amp * basis(u, v)
            if MODE == "last0":
                u2, v2 = ZZ[R + 1]
                blk = blk + sign * 14 * basis(u2, v2)
            Y[by : by + 8, bx : bx + 8] = 128 + blk
    return np.clip(np.round(Y), 0, 255).astype(np.uint8)


def run_frame(run_fn, amp):
    fp = DE.encode(
        mkY(run_fn, amp, 1).tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes(),
        W,
        H,
    )
    if fp is None:
        return None
    cfg = EX.config(fp)
    q = cfg[0]
    oyp = subprocess.run(
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
    if len(oyp) < W * H:
        return None
    Ytp = np.frombuffer(oyp[: W * H], np.uint8).reshape(H, W).astype(float)
    fn = DE.encode(
        mkY(run_fn, amp, -1).tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes(),
        W,
        H,
    )
    if fn is None or EX.config(fn) != cfg:
        return cfg
    bp = "".join(format(x, "08b") for x in fp)
    bn = "".join(format(x, "08b") for x in fn)
    mbw, mbh = W // 16, H // 16
    p = 17
    coded = np.zeros((2 * mbh, 2 * mbw), int)

    def fd_from(s):
        k = s
        while k < len(bp) and k < len(bn) and bp[k] == bn[k]:
            k += 1
        return k

    for my in range(mbh):
        for mx in range(mbw):
            L = None
            for Ln in range(1, 14):
                if bp[p : p + Ln] in mb_intra:
                    L = Ln
                    break
            if L is None:
                return cfg
            raw = mb_intra[bp[p : p + L]]
            p += L
            cbcr, cbpy = raw.split("_")
            rawb = [int(cbpy[i]) for i in range(4)]
            cbp = [0] * 4
            for i in range(4):
                bx = 2 * mx + (i % 2)
                by = 2 * my + (i // 2)
                A = coded[by][bx - 1] if bx > 0 else 0
                B = coded[by - 1][bx] if by > 0 else 0
                Cc = coded[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                cbp[i] = rawb[i] ^ (A if Cc == B else B)
                coded[by][bx] = cbp[i]
            ap = bp[p]
            p += 1
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                n = dclen(bp, p, tab)
                if n == 0:
                    return cfg
                p += n
                isc = cbp[blk] if blk < 4 else int(cbcr[blk - 4])
                if isc:
                    fd1 = fd_from(p)
                    code = bp[p:fd1]
                    end = fd1 + 1
                    if MODE == "last0":
                        end = fd_from(fd1 + 1) + 1  # skip anchor too
                    if blk < 4 and code and not code.startswith("0000011"):
                        bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                        C = Mm @ Ytp[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T
                        cands = sorted(
                            [
                                (
                                    abs(C[u, v]),
                                    ZZ.index((u, v)) if (u, v) in ZZ else 99,
                                    lvl(C, u, v, q),
                                    u,
                                    v,
                                )
                                for u in range(8)
                                for v in range(8)
                                if (u or v)
                            ],
                            reverse=True,
                        )
                        # target = lowest scan pos among significant
                        sig = [c for c in cands if c[0] >= q]
                        if sig:
                            tgt = min(sig, key=lambda c: c[1])
                            run = tgt[1]
                            lv = tgt[2]
                            last = 0 if MODE == "last0" else 1
                            if run < len(ZZ) and 1 <= lv <= 40:
                                tables[(cfg[1], cfg[2])][(run, lv, last)] = code
                    p = end
    return cfg


ncol = W // 8
amps = [
    18,
    26,
    36,
    48,
    62,
    80,
    100,
    125,
    150,
    40,
    55,
    70,
    90,
    110,
    135,
    30,
    22,
    45,
    65,
    85,
]
for t in range(N):
    cfg = run_frame(
        lambda bx, by: (bx + by * ncol) % (len(ZZ) - 2), amps[t % len(amps)]
    )
    print(
        f"t{t}: amp={amps[t%len(amps)]} cfg={cfg} | "
        + ", ".join(f"rlc{k[0]}rlt{k[1]}:{len(v)}" for k, v in tables.items())
    )
pickle.dump(
    {
        str(k): {f"{r},{l},{la}": c for (r, l, la), c in v.items()}
        for k, v in tables.items()
    },
    open(OUT, "wb"),
)
print(f"\nSaved {OUT}: " + ", ".join(f"{k}:{len(v)}" for k, v in tables.items()))
