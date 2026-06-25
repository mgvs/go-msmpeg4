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


def levelof(C, u, v):
    return round((abs(C[u, v]) / 4 - 1) / 2)


ZZ = R.ZZ_AC
table = {}  # (run,level,last) -> code
# LAST=1: single coefficient at ZZ[run], sweep amp for levels
for run in range(0, 16):
    u, v = ZZ[run]
    for amp in range(6, 60, 2):
        bp, Cp = enc(amp * R.BASIS[u][v])
        bn, Cn = enc(-amp * R.BASIS[u][v])
        lvl = levelof(Cp, u, v)
        if lvl < 1 or lvl > 12:
            continue
        # verify it's a SINGLE coef (no other large AC)
        others = [
            (uu, vv)
            for uu in range(8)
            for vv in range(8)
            if (uu or vv) and (uu, vv) != (u, v) and abs(Cp[uu, vv]) >= 8
        ]
        if others:
            continue
        ap = ac_start(bp)
        an = ac_start(bn)
        if ap is None or an is None:
            continue
        i = 0
        while ap + i < len(bp) and an + i < len(bn) and bp[ap + i] == bn[an + i]:
            i += 1
        code = bp[ap : ap + i]
        if code.startswith("0000011"):
            continue  # escape, skip for direct
        key = (run, lvl, 1)
        if key not in table:
            table[key] = code
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in table.items()},
    open("/tmp/rl_last1.json", "w"),
)
print(f"LAST=1 direct codes reversed: {len(table)}")
for k in sorted(table)[:20]:
    print(f"  {k}: {table[k]}")
