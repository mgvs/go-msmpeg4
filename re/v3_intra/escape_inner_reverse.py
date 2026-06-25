import os
import subprocess, numpy as np, json, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "re"))
import recon_loop as R

mbi_c = {v: k for k, v in json.load(open("/tmp/mb_intra_real.json")).items()}
rl = {
    tuple(int(x) for x in k.split(",")): v
    for k, v in json.load(open("/tmp/rl_full2.json")).items()
}


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
    return "".join(format(x, "08b") for x in open("s.bin", "rb").read())


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


ZZ = R.ZZ_AC
inner = {}  # code -> (run,lev,last) extracted from esc1 inner
# craft (run, level) high enough to ESCAPE via esc1, both last0(anchor) and last1
for run in range(0, 30):
    u, v = ZZ[run]
    for amp in range(20, 140, 2):
        # last1 (single coef)
        bp = enc(amp * R.BASIS[u][v])
        bn = enc(-amp * R.BASIS[u][v])
        for b1, b2 in [(bp, bn)]:
            a1 = ac_start(b1)
            a2 = ac_start(b2)
            if a1 is None or a2 is None:
                continue
            if b1[a1 : a1 + 7] != "0000011":
                continue  # only escapes
            if b1[a1 + 7] != "1":
                continue  # only esc1
            # inner = common prefix of pos/neg after 0000011+1, minus sign
            q1 = a1 + 8
            q2 = a2 + 8
            i = 0
            while b1[q1 + i] == b2[q2 + i]:
                i += 1
            code = b1[q1 : q1 + i]
            if code and not code.startswith("0000011"):
                inner.setdefault(code, (run, "?", 1))
# merge into rl table
merged = dict(rl)
for code, (run, lev, last) in inner.items():
    # determine via: this inner appeared for (run, last1). level=1 (smallest escape)
    key = (run, 1, last)
    if code not in [v for v in merged.values()]:
        merged[(run, 1, last)] = code  # tentative
json.dump(
    {f"{r},{l},{la}": c for (r, l, la), c in merged.items() if isinstance(l, int)},
    open("/tmp/rl_full3.json", "w"),
)
print(f"DONE: extracted {len(inner)} esc1-inner codes, merged total {len(merged)}")
for c, k in list(inner.items())[:10]:
    print(f"  inner {c} run{k[0]}")
