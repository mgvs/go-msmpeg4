"""Fast batch DivX3 encoder via the VFW client (vfwenc.exe) under Wine. One Wine startup
encodes a whole list of grayscale frames -> ~0.03s/frame. Replaces the slow/crashy
VirtualDub path. Black-box use of DivX3.11/mpg4c32 VFW codec (no disassembly)."""
import os

import subprocess, os, numpy as np

VFW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "re")


def encode_batch(frames_gray, W, H, q=10000):
    """frames_gray: list of (H,W) uint8 arrays. Returns list of DIV3 I-frame bytes (or None)."""
    data = b"".join(
        np.ascontiguousarray(f.astype(np.uint8)).tobytes() for f in frames_gray
    )
    open("/tmp/vb_in.gray", "wb").write(data)
    try:
        os.remove("/tmp/vb_out.bin")
    except FileNotFoundError:
        pass
    env = dict(os.environ, WINEDEBUG="-all", WINE_CPU_TOPOLOGY="1:0")
    subprocess.run(
        [
            "wine",
            "vfwenc.exe",
            "Z:\\tmp\\vb_in.gray",
            str(W),
            str(H),
            "Z:\\tmp\\vb_out.bin",
            str(len(frames_gray)),
            str(q),
        ],
        cwd=VFW_DIR,
        env=env,
        capture_output=True,
        timeout=600,
    )
    if not os.path.exists("/tmp/vb_out.bin"):
        return [None] * len(frames_gray)
    d = open("/tmp/vb_out.bin", "rb").read()
    p = 0
    out = []
    for _ in range(len(frames_gray)):
        if p + 4 > len(d):
            out.append(None)
            continue
        ln = int.from_bytes(d[p : p + 4], "little")
        p += 4
        out.append(bytes(d[p : p + ln]) if ln and p + ln <= len(d) else None)
        p += ln
    return out


if __name__ == "__main__":
    import extract_div3 as EX, collections

    fs = [
        np.fromfunction(lambda i, j: 90 + i * 1.0 + j * 0.7 + t * 2, (128, 128))
        for t in range(20)
    ]
    import time

    t0 = time.time()
    res = encode_batch(fs, 128, 128)
    ok = sum(1 for r in res if r)
    cfgs = collections.Counter(EX.config(r)[1:] for r in res if r)
    print(f"{ok}/20 ok in {time.time()-t0:.1f}s, configs(rlc,rlt,dc):", dict(cfgs))
