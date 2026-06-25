"""Reverse RL VLC tables for configs DivX3 emits (rlt=0,1; rlc=0,2) via single-coef
common-prefix. Frame: each 8x8 luma block = flat DC + one AC coef at scan pos `run`.
Encode +amp and -amp (DivX3); the two bitstreams differ ONLY at AC sign bits, so each
block's code = common prefix from its AC-start to the first pos/neg difference. run from
scan pos, level from oracle, last=1. Accumulate code->(run,level,last) PER config."""

import subprocess, numpy as np, json, sys, pickle, collections

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
]


def dclen(b, p, tab):
    for n in range(1, 36):
        if b[p : p + n] in tab:
            return n
    return 0


def lvl(C, u, v, q):
    a = abs(C[u, v])
    return int(round((a / q - 1) / 2)) if a >= q else 0


tables = collections.defaultdict(dict)  # config(rlc,rlt) -> {(run,lev,last):code}


def reverse_frame(run_for_block, amp, W, H):
    def mkY(sign):
        Y = np.full((H, W), 128.0)
        for by in range(0, H, 8):
            for bx in range(0, W, 8):
                r = run_for_block(bx // 8, by // 8)
                u, v = ZZ[r]
                Y[by : by + 8, bx : bx + 8] = 128 + sign * amp * basis(u, v)
        return np.clip(np.round(Y), 0, 255).astype(np.uint8)

    Yp = mkY(1)
    Yn = mkY(-1)
    fp = DE.encode(Yp.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes(), W, H)
    if fp is None:
        return
    cfg = EX.config(fp)
    q = cfg[0]
    # oracle of pos frame for levels
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
        return
    Ytp = np.frombuffer(oyp[: W * H], np.uint8).reshape(H, W).astype(float)
    fn = DE.encode(Yn.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes(), W, H)
    if fn is None:
        return
    if EX.config(fn) != cfg:
        return  # configs must match for prefix method
    bp = "".join(format(x, "08b") for x in fp)
    bn = "".join(format(x, "08b") for x in fn)
    mbw, mbh = W // 16, H // 16
    p = 17
    if len(bp) != len(bn):
        pass
    coded = np.zeros((2 * mbh, 2 * mbw), int)
    for my in range(mbh):
        for mx in range(mbw):
            L = None
            for Ln in range(1, 14):
                if bp[p : p + Ln] in mb_intra:
                    L = Ln
                    break
            if L is None:
                return
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
                C = coded[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                cbp[i] = rawb[i] ^ (A if C == B else B)
                coded[by][bx] = cbp[i]
            chroma = [int(cbcr[0]), int(cbcr[1])]
            ap = bp[p]
            p += 1
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                n = dclen(bp, p, tab)
                if n == 0:
                    return
                p += n
                isc = cbp[blk] if blk < 4 else chroma[blk - 4]
                if isc:
                    # AC: single coef -> code = common prefix until pos/neg differ (sign)
                    fd = p
                    while fd < len(bp) and fd < len(bn) and bp[fd] == bn[fd]:
                        fd += 1
                    code = bp[p:fd]
                    if blk < 4 and code and not code.startswith("0000011"):
                        bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                        C = Mm @ Ytp[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T
                        # dominant AC = target
                        cand = [
                            (abs(C[u, v]), u, v)
                            for u in range(8)
                            for v in range(8)
                            if (u or v)
                        ]
                        amp_, u, v = max(cand)
                        run = ZZ.index((u, v)) if (u, v) in ZZ else -1
                        lv = lvl(C, u, v, q)
                        if run >= 0 and 1 <= lv <= 40:
                            tables[(cfg[1], cfg[2])][(run, lv, 1)] = code
                    p = fd + 1  # skip code+sign
                # if not coded: nothing
            # chroma blocks similar (skip detailed for now)
    return cfg


W = H = 64
ncol = W // 8
N = int(sys.argv[1]) if len(sys.argv) > 1 else 14
amps = [18, 26, 36, 48, 62, 80, 100, 125, 150, 40, 55, 70, 90, 110, 135, 30]
for t in range(N):
    amp = amps[t % len(amps)]
    cfg = reverse_frame(lambda bx, by: (bx + by * ncol) % len(ZZ), amp, W, H)
    print(
        f"t{t}: amp={amp} cfg={cfg} | tables: "
        + ", ".join(f"{k}:{len(v)}" for k, v in tables.items())
    )
pickle.dump(
    {
        str(k): {f"{r},{l},{la}": c for (r, l, la), c in v.items()}
        for k, v in tables.items()
    },
    open("/tmp/rl_rev.pkl", "wb"),
)
print("\nFINAL per-config code counts:", {str(k): len(v) for k, v in tables.items()})
