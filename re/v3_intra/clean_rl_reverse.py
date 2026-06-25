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


def lvl(C, u, v):
    return round((abs(C[u, v]) / 4 - 1) / 2)


ZZ = R.ZZ_AC
table = {}
# LAST=1: single coef, fine amp sweep
for run in range(0, 32):
    u, v = ZZ[run]
    for amp in range(4, 150, 1):
        bp, Cp = enc(amp * R.BASIS[u][v])
        bn, Cn = enc(-amp * R.BASIS[u][v])
        l = lvl(Cp, u, v)
        if l < 1 or l > 30:
            continue
        # dominant = target position
        mx = max(
            ((abs(Cp[a][bb]), a, bb) for a in range(8) for bb in range(8) if a or bb)
        )
        if (mx[1], mx[2]) != (u, v):
            continue
        ap, an = ac_start(bp), ac_start(bn)
        if ap is None or an is None:
            continue
        i = 0
        while bp[ap + i] == bn[an + i]:
            i += 1
        code = bp[ap : ap + i]
        if code.startswith("0000011"):
            continue
        if (run, l, 1) not in table:
            table[(run, l, 1)] = code
# LAST=0: coef + anchor at next pos
for run in range(0, 30):
    u, v = ZZ[run]
    u2, v2 = ZZ[run + 1]
    for amp in range(6, 150, 1):
        bp, Cp = enc(amp * R.BASIS[u][v] + 12 * R.BASIS[u2][v2])
        bn, Cn = enc(-amp * R.BASIS[u][v] + 12 * R.BASIS[u2][v2])
        l = lvl(Cp, u, v)
        if l < 1 or l > 30:
            continue
        if lvl(Cp, u2, v2) < 1:
            continue  # anchor must survive
        ap, an = ac_start(bp), ac_start(bn)
        if ap is None or an is None:
            continue
        i = 0
        while bp[ap + i] == bn[an + i]:
            i += 1
        code = bp[ap : ap + i]
        if code.startswith("0000011"):
            continue
        if (run, l, 0) not in table:
            table[(run, l, 0)] = code
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in table.items()},
    open("/tmp/rl_full2.json", "w"),
)
codes = list(table.values())
coll = sum(1 for a in codes for c in codes if a != c and a.startswith(c))
print(
    f"DONE: {len(table)} codes ({sum(1 for k in table if k[2]==0)} last0, {sum(1 for k in table if k[2]==1)} last1), collisions={coll}"
)
