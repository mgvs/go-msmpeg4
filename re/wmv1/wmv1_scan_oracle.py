"""wmv1_scan_oracle.py — derive the WMV1 intra scan by decoder-oracle (read (u,v) from pixels).

We reuse the constant 44-bit prefix (WMV1 header + MCBPC[block0-only] + ac_pred=0 + block0 DC=0,
discovered earlier: MCBPC@35, dc_idx=1, rl_idx=2) and append a hand-built WMV1 THIRD-ESCAPE
(ESC3) that explicitly encodes (run=R, level=1, last=1). WMV1 places that single coefficient at
scan index R+1 (i += run+1). We patch the bytes into a real WMV1 AVI, decode with ffmpeg, DCT
block-0 and read the coefficient's DCT position (u,v) -> scan[R+1] = (u,v). No escape *decoding*
is needed; ffmpeg does the decode.
"""
import subprocess, os
import numpy as np
import wmv1_scan as S

W, H = 16, 16
TMP = "/tmp/wmv1"
PREFIX = "00001001011111001000110000111010111010101010"   # 44-bit const header+MCBPC+acpred+DC0
ESC_LUMA2 = "0000011"                                       # rl_idx=2 escape marker
DC1_L, DC1_C = "10", "00"                                   # dc_idx=1 level-0 codes (luma/chroma)
M = S.M


def host():
    import random
    rng = random.Random(5)
    y = np.array([rng.randrange(0, 256) for _ in range(W * H)], np.uint8).reshape(H, W)
    S.encode(y.astype(float))   # noise -> large packet to hold the patched frame
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    pb = data[:sizes[0]]
    avi = bytearray(open(f"{TMP}/c.avi", "rb").read())
    return avi, bytes(avi).find(pb), len(pb)

HOST, POFF, PLEN = host()


def esc3(run, level=1):
    # ESC3: esc + "00" + last(1) + level_len(3 -> ll=3) + run_len(2 -> +3=6) + run(6) + sign + level(3)
    return (ESC_LUMA2 + "00" + "1" + "011" + "11" +
            format(run, "06b") + "0" + format(level, "03b"))


def decode_uv(run):
    bits = PREFIX + esc3(run) + DC1_L*3 + DC1_C*2
    while len(bits) % 8:
        bits += "0"
    b = bytearray(int(bits[i:i+8], 2) for i in range(0, len(bits), 8))
    if len(b) < PLEN:
        b += bytes(PLEN - len(b))
    avi = bytearray(HOST)
    avi[POFF:POFF+PLEN] = bytes(b[:PLEN])
    open(f"{TMP}/t.avi", "wb").write(avi)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/t.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    if len(out) < W*H:
        return None
    y = np.frombuffer(out[:W*H], np.uint8).reshape(H, W).astype(float)
    F = M @ (y[:8, :8] - y[:8, :8].mean()) @ M.T
    best = max(((abs(F[u, v]), u, v) for u in range(8) for v in range(8) if (u or v)))
    return (best[1], best[2]) if best[0] > 2 else None


if __name__ == "__main__":
    scan = {0: (0, 0)}
    for R in range(63):
        uv = decode_uv(R)
        if uv is not None:
            scan[R + 1] = uv
    miss = [k for k in range(64) if k not in scan]
    vals = list(scan.values())
    print(f"recovered {len(scan)}/64; permutation = {len(set(vals)) == len(vals) == 64}; missing {miss}")
    for k in range(64):
        print(f"  {k:2d}: {scan.get(k)}")
