"""Complete luma rl_table[2] — FAST batched black-box sweep of the ffmpeg msmpeg4 encoder.
All single-coef frames encoded as all-I (-g 1) in ONE ffmpeg call; decoded in one call.
amp = 8*level+3 hits each quantised level directly. Code = pos/neg common prefix.
Clean: encoder = black box, decoder = pixel oracle. No source code used."""

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
]


def blk_yuv(blk8):
    Y = np.clip(
        np.round(np.full((16, 16), 128.0) + np.pad(blk8, ((0, 8), (0, 8)))), 0, 255
    ).astype(np.uint8)
    return Y.tobytes() + bytes([128] * 128)


# build jobs
jobs = []  # (run, level, last)
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 26
for run in range(RUNS):
    for level in range(1, 36):
        jobs.append((run, level, 1))
        if run + 1 < len(ZZ_AC):
            jobs.append((run, level, 0))
# frames: pos, neg per job
frames = bytearray()
for run, level, last in jobs:
    u, v = ZZ_AC[run]
    amp = 8 * level + 3
    for sign in (1, -1):
        blk = sign * amp * basis(u, v)
        if last == 0:
            u2, v2 = ZZ_AC[run + 1]
            blk = blk + sign * 11 * basis(u2, v2)
        frames += blk_yuv(blk)
open("/tmp/rb.yuv", "wb").write(frames)
print(f"{len(jobs)} jobs, {2*len(jobs)} frames; encoding all-I...", flush=True)
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
        "/tmp/rb.yuv",
        "-c:v",
        "msmpeg4",
        "-qscale:v",
        "4",
        "-g",
        "1",
        "-frames:v",
        str(2 * len(jobs)),
        "-vtag",
        "DIV3",
        "/tmp/rb.avi",
    ],
    stderr=subprocess.DEVNULL,
)
import extract_div3 as EX

ifr = EX.iframes("/tmp/rb.avi", maxf=2 * len(jobs) + 5)
oy = subprocess.run(
    [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        "/tmp/rb.avi",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "yuv420p",
        "-",
    ],
    capture_output=True,
).stdout
print(f"got {len(ifr)} I-frames, oracle {len(oy)//384} frames", flush=True)


def ac_start(b):
    p = 17
    m = None
    for L in range(1, 14):
        if b[p : p + L] in mb_intra:
            m = b[p : p + L]
            break
    if m is None or mb_intra[m].split("_")[1][0] != "1":
        return None
    p += len(m) + 1
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


table = {}
for j, (run, level, last) in enumerate(jobs):
    if 2 * j + 1 >= len(ifr):
        break
    bp = "".join(format(x, "08b") for x in ifr[2 * j])
    bn = "".join(format(x, "08b") for x in ifr[2 * j + 1])
    ap, an = ac_start(bp), ac_start(bn)
    if ap is None or an is None:
        continue
    i = 0
    while ap + i < len(bp) and an + i < len(bn) and bp[ap + i] == bn[an + i]:
        i += 1
    code = bp[ap : ap + i]
    if not code or code.startswith("0000011"):
        continue
    if (run, level, last) not in table:
        table[(run, level, last)] = code
match = mism = 0
for k in set(table) & set(ours):
    if table[k] == ours[k]:
        match += 1
    else:
        mism += 1
codes = list(table.values())
coll = sum(1 for a in codes for c in codes if a != c and c.startswith(a))
print(
    f"\nDERIVED {len(table)} codes; vs ours common={len(set(table)&set(ours))} MATCH={match} MISMATCH={mism}"
)
print(
    f"prefix-collisions={coll}; NEW (not in ours)={len(set(table)-set(ours))}; ours has {len(ours)}"
)
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in table.items()},
    open("/tmp/rl2_derived.json", "w"),
)
