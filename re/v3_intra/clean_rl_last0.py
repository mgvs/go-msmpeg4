import os
import subprocess, numpy as np, json, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "re"))
import recon_loop as R

mbi_c = {v: k for k, v in json.load(open("/tmp/mb_intra_real.json")).items()}


def fdct(Yb):
    M = np.zeros((8, 8))
    for k in range(8):
        for n in range(8):
            ck = (1 / np.sqrt(2)) if k == 0 else 1
            M[k, n] = 0.5 * ck * np.cos((2 * n + 1) * k * np.pi / 16)
    return M @ Yb @ M.T


def enc(blk8):
    Y = np.full((16, 16), 128.0)
    Y[:8, :8] = 128 + blk8
    Y = np.clip(np.round(Y), 0, 255).astype(np.uint8)
    open("s.yuv", "wb").write(Y.tobytes() + bytes([128] * 128))
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
            "s.yuv",
            "-c:v",
            "msmpeg4",
            "-qscale:v",
            "4",
            "-frames:v",
            "1",
            "-vtag",
            "DIV3",
            "s.avi",
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
            "s.avi",
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-frames:v",
            "1",
            "-f",
            "data",
            "s.bin",
        ],
        stderr=subprocess.DEVNULL,
    )
    b = "".join(format(x, "08b") for x in open("s.bin", "rb").read())
    o = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "s.avi",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "yuv420p",
            "-",
        ],
        capture_output=True,
    ).stdout
    Yb = (
        np.frombuffer(o[:256], dtype=np.uint8).reshape(16, 16)[:8, :8].astype(float)
        - 128
    )
    return b, fdct(Yb)


def ac_start(b):
    p = 17
    m = None
    for L in range(1, 14):
        if b[p : p + L] in mbi_c:
            m = b[p : p + L]
            break
    if m is None:
        return None
    p += len(m) + 1
    c = ""
    n = 0
    while n < 36:
        c += b[p + n]
        n += 1
        if c in R.DCL:
            break
    else:
        return None
    return p + n


def lv(C, u, v):
    return round((abs(C[u, v]) / 4 - 1) / 2)


ZZ = R.ZZ_AC
table = json.load(open("/tmp/rl_last1.json"))  # start with last1
table = {tuple(int(x) for x in k.split(",")): v for k, v in table.items()}
# LAST=0: coef at ZZ[run] (last0) + anchor at ZZ[run+1] (last1)
for run in range(0, 12):
    u, v = ZZ[run]
    u2, v2 = ZZ[run + 1]
    for amp in range(8, 56, 3):
        b1, C1 = enc(amp * R.BASIS[u][v] + 14 * R.BASIS[u2][v2])
        b2, C2 = enc(-amp * R.BASIS[u][v] + 14 * R.BASIS[u2][v2])
        l = lv(C1, u, v)
        if l < 1 or l > 10:
            continue
        # require coef at ZZ[run] and anchor at ZZ[run+1] both present, nothing else
        oth = [
            (a, bb)
            for a in range(8)
            for bb in range(8)
            if (a or bb) and (a, bb) not in [(u, v), (u2, v2)] and abs(C1[a, bb]) >= 8
        ]
        if oth:
            continue
        ap1 = ac_start(b1)
        ap2 = ac_start(b2)
        if ap1 is None or ap2 is None:
            continue
        i = 0
        while b1[ap1 + i] == b2[ap2 + i]:
            i += 1
        code = b1[ap1 : ap1 + i]
        if code.startswith("0000011"):
            continue
        key = (run, l, 0)
        if key not in table:
            table[key] = code
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in table.items()},
    open("/tmp/rl_full.json", "w"),
)
n0 = sum(1 for k in table if k[2] == 0)
n1 = sum(1 for k in table if k[2] == 1)
print(f"rl_table[2] direct: {len(table)} codes (last0={n0} last1={n1})")
# prefix-free check
codes = list(table.values())
coll = sum(1 for a in codes for c in codes if a != c and a.startswith(c))
print(f"prefix-collisions among direct: {coll}")
# check escape entry not conflicting
print("any direct code starts 0000011?", any(c.startswith("0000011") for c in codes))
