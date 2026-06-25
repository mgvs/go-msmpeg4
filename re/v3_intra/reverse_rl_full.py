"""Complete the luma rl_table[2] by a THOROUGH single-coefficient black-box sweep of the
ffmpeg msmpeg4 encoder (clean: encoder=black box, decoder=pixel oracle; no source).
Each block has one AC coef at scan position `run` (+anchor for last=0); encode +amp/-amp,
the code = common prefix (sign bit is the first pos/neg difference). level from the oracle.
"""

import subprocess, numpy as np, json, sys

mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
dctab_l = json.load(open("data/dc_luma.json"))
ours = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_table2.json")).items()
}
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


ZZ_AC = [
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
    (2, 5),
    (3, 4),
    (4, 3),
    (5, 2),
    (6, 1),
    (7, 0),
    (7, 1),
    (6, 2),
    (5, 3),
    (4, 4),
    (3, 5),
    (2, 6),
    (1, 7),
    (2, 7),
    (3, 6),
    (4, 5),
    (5, 4),
    (6, 3),
    (7, 2),
    (7, 3),
    (6, 4),
    (5, 5),
    (4, 6),
    (3, 7),
    (4, 7),
    (5, 6),
    (6, 5),
    (7, 4),
    (7, 5),
    (6, 6),
    (5, 7),
    (6, 7),
    (7, 6),
    (7, 7),
]


def enc(blk8, qs=4):
    Y = np.full((16, 16), 128.0)
    Y[:8, :8] = 128 + blk8
    Y = np.clip(np.round(Y), 0, 255).astype(np.uint8)
    open("/tmp/s.yuv", "wb").write(Y.tobytes() + bytes([128] * 128))
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
            "/tmp/s.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            str(qs),
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            "/tmp/s.avi",
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
            "/tmp/s.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            "/tmp/s.bin",
        ],
        stderr=subprocess.DEVNULL,
    )
    b = "".join(format(x, "08b") for x in open("/tmp/s.bin", "rb").read())
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "/tmp/s.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    Yb = np.frombuffer(o[:256], np.uint8).reshape(16, 16)[:8, :8].astype(float) - 128
    return b, Mm @ Yb @ Mm.T


def ac_start(b):
    p = 17
    m = None
    for L in range(1, 14):
        if b[p : p + L] in mb_intra:
            m = b[p : p + L]
            break
    if m is None:
        return None
    raw = mb_intra[m]
    p += len(m)
    cbpy = raw.split("_")[1]
    if cbpy[0] != "1":
        return None  # block0 must be coded
    p += 1  # acpred
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in dctab_l:
            break
    else:
        return None
    return p + n


def lvl(C, u, v, q=4):
    a = abs(C[u, v])
    return int(round((a - (q - 1 if q % 2 == 0 else 0)) / (2 * q))) if a >= q else 0


table = {}


def add(run, last, basisblk_fn):
    for amp in range(6, 170, 1):
        bp, Cp = enc(basisblk_fn(amp))
        bn, Cn = enc(basisblk_fn(-amp))
        u, v = ZZ_AC[run]
        l = lvl(Cp, u, v)
        if l < 1 or l > 40:
            continue
        # dominant coef = target position (for last=1) / target is lowest scan among sig (last=0 with anchor)
        sig = [
            (abs(Cp[a][bb]), a, bb)
            for a in range(8)
            for bb in range(8)
            if (a or bb) and abs(Cp[a][bb]) >= 4
        ]
        if not sig:
            continue
        ap, an = ac_start(bp), ac_start(bn)
        if ap is None or an is None:
            continue
        i = 0
        while ap + i < len(bp) and an + i < len(bn) and bp[ap + i] == bn[an + i]:
            i += 1
        code = bp[ap : ap + i]
        if not code or code.startswith("0000011"):
            continue  # skip escape
        if (run, l, last) not in table:
            table[(run, l, last)] = code


N = int(sys.argv[1]) if len(sys.argv) > 1 else 63
for run in range(N):
    u, v = ZZ_AC[run]
    add(run, 1, lambda amp, u=u, v=v: amp * basis(u, v))  # last=1: single coef
    if run + 1 < 63:
        u2, v2 = ZZ_AC[run + 1]
        add(
            run,
            0,
            lambda amp, u=u, v=v, u2=u2, v2=v2: amp * basis(u, v) + 12 * basis(u2, v2),
        )  # last=0 + anchor
    print(f"run {run}: table now {len(table)} codes", flush=True)
# verify vs ours + completeness
match = mism = 0
for k in set(table) & set(ours):
    if table[k] == ours[k]:
        match += 1
    else:
        mism += 1
codes = list(table.values())
coll = sum(1 for a in codes for c in codes if a != c and c.startswith(a))
print(
    f"\nDERIVED {len(table)} codes ({sum(1 for k in table if k[2]==0)} last0, {sum(1 for k in table if k[2]==1)} last1)"
)
print(f"vs ours: MATCH={match} MISMATCH={mism} (of {len(set(table)&set(ours))} common)")
print(f"prefix-collisions={coll}; new codes (not in ours): {len(set(table)-set(ours))}")
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in table.items()},
    open("/tmp/rl2_derived.json", "w"),
)
