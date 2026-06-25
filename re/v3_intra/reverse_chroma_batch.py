"""Complete the chroma RL table by a FAST batched black-box sweep of the ffmpeg msmpeg4
encoder. Each 16x16 frame: flat luma, ONE AC coef in the Cb (U) block at scan position
`run` (+anchor for last=0); all-I batch encode; pos/neg common-prefix = code.
Chroma is a single block per MB (no multi-block ambiguity). amp=8*level+3 hits level@q4.
Clean: encoder=black box, decoder=pixel oracle; no source used for the committed table.
"""

import subprocess, numpy as np, json, sys

mb_intra = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}
dctab_l = json.load(open("data/dc_luma.json"))
dctab_c = json.load(open("data/dc_chroma.json"))
ourc = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("data/rl_chroma.json")).items()
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


def cb_yuv(cblk8):
    Y = bytes([128] * 256)
    U = np.clip(np.round(128 + cblk8), 0, 255).astype(np.uint8).tobytes()
    V = bytes([128] * 64)
    return Y + U + V


RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 26
jobs = []
for run in range(RUNS):
    for level in range(1, 36):
        jobs.append((run, level, 1))
        if run + 1 < len(ZZ_AC):
            jobs.append((run, level, 0))
frames = bytearray()
for run, level, last in jobs:
    u, v = ZZ_AC[run]
    amp = 8 * level + 3
    for sign in (1, -1):
        blk = sign * amp * basis(u, v)
        if last == 0:
            u2, v2 = ZZ_AC[run + 1]
            blk = blk + sign * 11 * basis(u2, v2)
        frames += cb_yuv(blk)
open("/tmp/cb.yuv", "wb").write(frames)
print(f"{len(jobs)} jobs, {2*len(jobs)} frames; encoding...", flush=True)
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
        "/tmp/cb.yuv",
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
        "/tmp/cb.avi",
    ],
    stderr=subprocess.DEVNULL,
)
import extract_div3 as EX

ifr = EX.iframes("/tmp/cb.avi", maxf=2 * len(jobs) + 5)
print(f"got {len(ifr)} I-frames", flush=True)


def cb_ac_start(b):
    p = 17
    m = None
    for L in range(1, 14):
        if b[p : p + L] in mb_intra:
            m = b[p : p + L]
            break
    if m is None:
        return None
    cbcr, cbpy = mb_intra[m].split("_")
    if cbcr[0] != "1":
        return None  # blk4 (Cb) must be coded
    p += len(m) + 1  # +acpred
    for blk in range(4):  # luma DCs
        c = ""
        n = 0
        while n < 36:
            c += b[p + n]
            n += 1
            if c in dctab_l:
                break
        else:
            return None
        p += n
        # luma cbp: flat -> 0, no AC. cbpy bits
        if cbpy[blk] == "1":  # would have AC; skip it (shouldn't happen for flat)
            return None
    # blk4 chroma DC
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in dctab_c:
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
    ap, an = cb_ac_start(bp), cb_ac_start(bn)
    if ap is None or an is None:
        continue
    i = 0
    while ap + i < len(bp) and an + i < len(bn) and bp[ap + i] == bn[an + i]:
        i += 1
    code = bp[ap : ap + i]
    if not code or code.startswith("101101001"):
        continue  # skip escape
    if (run, level, last) not in table:
        table[(run, level, last)] = code
# verify vs table4 (correctness) + merge with ours
t4 = None
import os

if os.path.exists("/tmp/chroma_full.json"):
    t4 = {
        tuple(int(x) for x in k.split(",")): v
        for k, v in json.load(open("/tmp/chroma_full.json")).items()
    }
match = mism = 0
if t4:
    for k in set(table) & set(t4):
        if table[k] == t4[k]:
            match += 1
        else:
            mism += 1
codes = list(table.values())
coll = sum(1 for a in codes for c in codes if a != c and c.startswith(a))
print(f"\nDERIVED {len(table)} chroma codes (black-box)")
if t4:
    print(
        f"vs table4: common={len(set(table)&set(t4))} MATCH={match} MISMATCH={mism}  (table4 has 168)"
    )
print(
    f"vs ours-111: agree={sum(1 for k in set(table)&set(ourc) if table[k]==ourc[k])}/{len(set(table)&set(ourc))}"
)
print(f"prefix-collisions={coll}; NEW beyond ours={len(set(table)-set(ourc))}")
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in table.items()},
    open("/tmp/chroma_derived.json", "w"),
)
