"""Robust fast RL reversal: ONE coef-block per 16x16 frame (rest flat). pos & neg batches.
For each frame: parse header+mb_code+acpred+block0 DC, then code = block0 AC = common
prefix until the (only) pos/neg difference = sign bit. run from scan, level from oracle.
Group by config (rlc,rlt), dc=1 only. Hundreds of frames per Wine startup -> fast+clean.
"""

import subprocess, numpy as np, json, sys, pickle, collections

sys.path.insert(0, ".")
import divx_batch as DB, extract_div3 as EX

mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
dctab_l = json.load(open("data/dc_luma.json"))
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
]


def dclen(b, p, tab):
    for n in range(1, 36):
        if b[p : p + n] in tab:
            return n
    return 0


def lvl(C, u, v, q):
    a = abs(C[u, v])
    return int(round((a / q - 1) / 2)) if a >= q else 0


def mkframe(run, amp, sign):
    Y = np.full((16, 16), 128.0)
    Y[:8, :8] = 128 + sign * amp * basis(*ZZ[run])
    return np.clip(np.round(Y), 0, 255).astype(np.uint8)


def oracle(frame):
    key = (16, 16)
    if not hasattr(oracle, "sk"):
        Yg = (np.arange(256).reshape(16, 16) % 200 + 20).astype(np.uint8)
        open("/tmp/sk.yuv", "wb").write(
            Yg.tobytes() + np.full(128, 128, np.uint8).tobytes()
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
                "16x16",
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
        oracle.sk = (skf, av, av.find(skf))
    skf, av, off = oracle.sk
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
    return (
        np.frombuffer(o[:256], np.uint8).reshape(16, 16)[:8, :8].astype(float)
        if len(o) >= 256
        else None
    )


tables = collections.defaultdict(dict)


def process(fp, fn):
    if fp is None or fn is None:
        return
    cfg = EX.config(fp)
    if cfg != EX.config(fn) or cfg[3] != 1:
        return
    q = cfg[0]
    Yb = oracle(fp)
    if Yb is None:
        return
    C = Mm @ (Yb - 128) @ Mm.T
    sig = [
        (abs(C[u, v]), ZZ.index((u, v)) if (u, v) in ZZ else 99, lvl(C, u, v, q))
        for u in range(8)
        for v in range(8)
        if (u or v) and abs(C[u, v]) >= q
    ]
    if not sig:
        return
    _, run, lev = min(sig, key=lambda c: c[1])
    if run >= len(ZZ) or not (1 <= lev <= 40):
        return
    bp = "".join(format(x, "08b") for x in fp)
    bn = "".join(format(x, "08b") for x in fn)
    p = 17
    L = None
    for Ln in range(1, 14):
        if bp[p : p + Ln] in mb_intra:
            L = Ln
            break
    if L is None:
        return
    p += L + 1  # mb_code + acpred
    n = dclen(bp, p, dctab_l)  # block0 DC
    if n == 0:
        return
    p += n
    fd = p
    while fd < len(bp) and fd < len(bn) and bp[fd] == bn[fd]:
        fd += 1
    code = bp[p:fd]
    if code and not code.startswith("0000011"):
        tables[(cfg[1], cfg[2])][(run, lev, 1)] = code


W = H = 16
# cover run x amp
jobs = []
for run in range(len(ZZ)):
    for amp in [16, 20, 25, 31, 38, 46, 56, 68, 82, 98, 116, 136, 150]:
        jobs.append((run, amp))
posf = [mkframe(r, a, 1) for r, a in jobs]
negf = [mkframe(r, a, -1) for r, a in jobs]
print(f"encoding {len(jobs)} pos + {len(jobs)} neg frames (16x16)...")
fp = DB.encode_batch(posf, 16, 16)
fn = DB.encode_batch(negf, 16, 16)
for i in range(len(jobs)):
    process(fp[i], fn[i])
print("per-config:", {f"rlc{k[0]}rlt{k[1]}": len(v) for k, v in tables.items()})
for k, v in sorted(tables.items()):
    codes = list(v.values())
    coll = sum(1 for a in codes for c in codes if a != c and a.startswith(c))
    print(f"  rlc{k[0]}rlt{k[1]}: {len(v)} codes, collisions={coll}")
pickle.dump(
    {
        str(k): {f"{r},{l},{la}": c for (r, l, la), c in v.items()}
        for k, v in tables.items()
    },
    open("/tmp/rl_b2.pkl", "wb"),
)
