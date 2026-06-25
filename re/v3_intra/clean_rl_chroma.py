"""Clean reverse of the CHROMA rl_table (rl_table[3+chroma_idx]) — craft chroma
coefficients (color content), pos/neg -> code+sign, same method as luma."""

import subprocess, numpy as np, json, sys

sys.path.insert(0, ".")
import recon_loop as R

DCL = json.load(open("data/dc_luma.json"))
DCC = json.load(open("data/dc_chroma.json"))
mbi_c = {v: k for k, v in json.load(open("data/table_mb_intra_raw.json")).items()}


def fdct(Yb):
    M = np.array(
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
    return M @ Yb @ M.T


def enc(cb8):
    Y = np.full((16, 16), 128, np.uint8)
    Cb = np.clip(np.round(128 + cb8), 0, 255).astype(np.uint8)
    Cr = np.full((8, 8), 128, np.uint8)
    open("s.yuv", "wb").write(Y.tobytes() + Cb.tobytes() + Cr.tobytes())
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
    Cbd = np.frombuffer(o[256 : 256 + 64], np.uint8).reshape(8, 8).astype(float) - 128
    return b, fdct(Cbd)


def ac_start_chroma(b):
    # header + mb_code + ac_pred + 4 luma DC (all level0, no AC since gray) + Cb DC
    p = 17
    m = None
    for L in range(1, 14):
        if b[p : p + L] in mbi_c:
            m = b[p : p + L]
            break
    if m is None:
        return None
    p += len(m) + 1
    for blk in range(5):  # 4 luma + Cb DC
        tab = DCL if blk < 4 else DCC
        c = ""
        n = 0
        while n < 36:
            c += b[p + n]
            n += 1
            if c in tab:
                break
        else:
            return None
        p += n
    return p


ZZ = R.ZZ_AC
table = {}
for run in range(0, 16):
    u, v = ZZ[run]
    for amp in range(4, 80, 1):
        bp, Cp = enc(amp * R.BASIS[u][v])
        bn, Cn = enc(-amp * R.BASIS[u][v])
        l = round((abs(Cp[u][v]) / 4 - 1) / 2)
        if l < 1 or l > 11:
            continue
        mx = max(
            ((abs(Cp[a][bb]), a, bb) for a in range(8) for bb in range(8) if a or bb)
        )
        if (mx[1], mx[2]) != (u, v):
            continue
        ap, an = ac_start_chroma(bp), ac_start_chroma(bn)
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
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in table.items()},
    open("data/rl_chroma_last1.json", "w"),
)
print(f"CHROMA last1: {len(table)} codes")
for k in sorted(table)[:8]:
    print(f"  {k}={table[k]}")
