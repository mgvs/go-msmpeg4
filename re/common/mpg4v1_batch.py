"""Fast batch MS-MPEG4 v1 encoder via the VFW client (vfwenc_mpg4.exe) under Wine. One Wine startup
encodes a whole list of grayscale frames -> ~0.03s/frame. Replaces the slow/crashy
VirtualDub path. Black-box use of MS-MPEG4 v1.11/mpg4c32 VFW codec (no disassembly)."""
import os

import subprocess, os, numpy as np

VFW_DIR = os.path.dirname(os.path.abspath(__file__))  # re/common (where vfwenc_mpg4.exe lives)


def encode_batch_v1(frames_gray, W, H, q=10000):
    """frames_gray: list of (H,W) uint8 arrays. Returns list of MPG4 v1 I-frame bytes (or None)."""
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
            "vfwenc_mpg4.exe",
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


