"""Fast RL table reversal via VFW batch encoder. Single-coef frames (varied run/block),
pos & neg batches; per frame where pos/neg config matches AND dc=1 (our DC table),
extract each block's code = common prefix (sign bit = first pos/neg diff). Group by
(rlc,rlt). last=1. Fast: hundreds of frames per Wine startup."""

import subprocess, numpy as np, json, sys, pickle, collections, os

sys.path.insert(0, ".")
import divx_batch as DB, extract_div3 as EX

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
]


def dclen(b, p, tab):
    for n in range(1, 36):
        if b[p : p + n] in tab:
            return n
    return 0


def lvl(C, u, v, q):
    a = abs(C[u, v])
    return int(round((a / q - 1) / 2)) if a >= q else 0


def mkframe(amp, sign, W, H):
    Y = np.full((H, W), 128.0)
    ncol = W // 8
    for by in range(0, H, 8):
        for bx in range(0, W, 8):
            R = ((bx // 8) + (by // 8) * ncol) % (len(ZZ) - 1)
            u, v = ZZ[R]
            Y[by : by + 8, bx : bx + 8] = 128 + sign * amp * basis(u, v)
    return np.clip(np.round(Y), 0, 255).astype(np.uint8)


tables = collections.defaultdict(dict)


def process(fp, fn, W, H):
    if fp is None or fn is None:
        return
    cfg = EX.config(fp)
    if cfg != EX.config(fn) or cfg[3] != 1:
        return  # need matching config & dc=1
    q = cfg[0]
    # oracle (decode pos with ffmpeg)
    open("/tmp/rb.avi", "wb").write(
        b""
    )  # need an avi; instead decode raw frame via wrapping? use ffmpeg on raw div3?
    bp = "".join(format(x, "08b") for x in fp)
    bn = "".join(format(x, "08b") for x in fn)
    mbw, mbh = W // 16, H // 16
    p = 17
    coded = np.zeros((2 * mbh, 2 * mbw), int)
    # oracle via ffmpeg: wrap fp into the skeleton? simpler: decode using our own? -> need oracle for level.
    # Use ffmpeg by writing a minimal AVI around fp:
    oy = decode_oracle(fp, W, H)
    if oy is None:
        return
    Ytp = np.frombuffer(oy[: W * H], np.uint8).reshape(H, W).astype(float)

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
                Cc = coded[by - 1][bx - 1] if (bx > 0 and by > 0) else 0
                cbp[i] = rawb[i] ^ (A if Cc == B else B)
                coded[by][bx] = cbp[i]
            ap = bp[p]
            p += 1
            for blk in range(6):
                tab = dctab_l if blk < 4 else dctab_c
                n = dclen(bp, p, tab)
                if n == 0:
                    return
                p += n
                isc = cbp[blk] if blk < 4 else int(cbcr[blk - 4])
                if isc:
                    fd1 = fd_from(p)
                    code = bp[p:fd1]
                    if blk < 4 and code and not code.startswith("0000011"):
                        bx, by = 2 * mx + (blk % 2), 2 * my + (blk // 2)
                        C = Mm @ Ytp[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] @ Mm.T
                        sig = [
                            (
                                abs(C[u, v]),
                                ZZ.index((u, v)) if (u, v) in ZZ else 99,
                                lvl(C, u, v, q),
                            )
                            for u in range(8)
                            for v in range(8)
                            if (u or v) and abs(C[u, v]) >= q
                        ]
                        if sig:
                            _, run, lv = min(sig, key=lambda c: c[1])
                            if run < len(ZZ) and 1 <= lv <= 40:
                                tables[(cfg[1], cfg[2])][(run, lv, 1)] = code
                    p = fd1 + 1
    return cfg


def decode_oracle(frame, W, H):
    # wrap raw DIV3 frame into a skeleton AVI of same WxH (cached) and decode with ffmpeg
    import struct

    key = (W, H)
    if key not in decode_oracle.sk:
        # build skeleton via vfwenc on a known frame, get its avi via ffmpeg? simpler: use ffmpeg to make a DIV3 avi
        Yg = (np.arange(W * H).reshape(H, W) % 200 + 20).astype(np.uint8)
        open("/tmp/sk.yuv", "wb").write(
            Yg.tobytes() + np.full(W * H // 2, 128, np.uint8).tobytes()
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "yuv420p",
                "-s",
                f"{W}x{H}",
                "-i",
                "/tmp/sk.yuv",
                "-c:v",
                "msmpeg4",
                "-qscale:v",
                "4",
                "-frames:v",
                "1",
                "-vtag",
                "DIV3",
                "/tmp/sk.avi",
            ],
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                "/tmp/sk.avi",
                "-map",
                "0:v:0",
                "-c",
                "copy",
                "-frames:v",
                "1",
                "-f",
                "data",
                "/tmp/skf.bin",
            ],
            stderr=subprocess.DEVNULL,
        )
        skf = open("/tmp/skf.bin", "rb").read()
        av = bytearray(open("/tmp/sk.avi", "rb").read())
        off = av.find(skf)
        decode_oracle.sk[key] = (skf, av, off)
    skf, av, off = decode_oracle.sk[key]
    if len(frame) > len(skf):
        return None
    a = bytearray(av)
    a[off : off + len(skf)] = frame + skf[len(frame) :]
    open("/tmp/ro.avi", "wb").write(a)
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/ro.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    return o[: W * H] if len(o) >= W * H else None


decode_oracle.sk = {}
W = H = 64
NB = int(sys.argv[1]) if len(sys.argv) > 1 else 60
amps = [
    18,
    24,
    30,
    38,
    46,
    56,
    68,
    82,
    98,
    116,
    136,
    22,
    28,
    35,
    44,
    54,
    66,
    80,
    96,
    114,
    134,
    20,
    26,
    33,
    42,
]
posf = [mkframe(amps[t % len(amps)], 1, W, H) for t in range(NB)]
negf = [mkframe(amps[t % len(amps)], -1, W, H) for t in range(NB)]
fp = DB.encode_batch(posf, W, H)
fn = DB.encode_batch(negf, W, H)
for i in range(NB):
    process(fp[i], fn[i], W, H)
print("per-config codes:", {f"rlc{k[0]}rlt{k[1]}": len(v) for k, v in tables.items()})
for k, v in tables.items():
    codes = list(v.values())
    coll = sum(1 for a in codes for c in codes if a != c and a.startswith(c))
    print(f"  {k}: {len(v)} codes, prefix-collisions={coll}")
pickle.dump(
    {
        str(k): {f"{r},{l},{la}": c for (r, l, la), c in v.items()}
        for k, v in tables.items()
    },
    open("/tmp/rl_batch.pkl", "wb"),
)
