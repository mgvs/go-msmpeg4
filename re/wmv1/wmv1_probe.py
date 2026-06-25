"""wmv1_probe.py — examine the WMV1 (Windows Media Video 7) I-frame bitstream structure
to bootstrap scan-table derivation. Black-box: ffmpeg encoder/decoder only.

WMV1 reuses the v3 VLC tables (MV/MB/RL/MCBPC/DC) but uses different scan tables and
DC-scale tables, and a slightly different picture header (per_mb_rl_table flag). We encode
controlled intra content as WMV1 and dump the first bytes so we can locate the header
fields and the first MB's MCBPC/DC/AC, then compare against the known v3 layout.
"""
import subprocess, os, random
import numpy as np

W, H = 16, 16
TMP = "/tmp/wmv1"
os.makedirs(TMP, exist_ok=True)


def encode_wmv1(y, cb, cr, q=4):
    raw = (np.asarray(y, np.uint8).tobytes() +
           np.asarray(cb, np.uint8).tobytes() + np.asarray(cr, np.uint8).tobytes())
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "wmv1", "-qscale:v", str(q),
                    "-frames:v", "1", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    return data[:sizes[0]]


def bits(b):
    return "".join(format(x, "08b") for x in b)


if __name__ == "__main__":
    flat = np.full((H, W), 128)
    chroma = np.full((H // 2, W // 2), 128)
    # 1) flat frame: minimal MB content, exposes header layout
    p = encode_wmv1(flat, chroma, chroma)
    b = bits(p)
    print(f"flat WMV1 I-frame: {len(p)} bytes")
    print(f"  bits[:40] = {b[:40]}")
    # tentative v3-style parse: pictype(2) quant(5) then 5 'remaining' then c3 c3 dc
    print(f"  pictype={b[:2]} quant={int(b[2:7],2)} next13={b[7:20]}")
    # 2) one strong AC coefficient in luma block0 -> see where the encoder/scan place it
    M = np.array([[0.5*(1/np.sqrt(2) if k==0 else 1)*np.cos((2*n+1)*k*np.pi/16) for n in range(8)] for k in range(8)])
    for (u, v) in [(0, 1), (1, 0), (0, 2), (2, 0)]:
        basis = M.T[:, u][:, None] @ M.T[:, v][None, :]  # idct of unit coeff at (u,v)
        blk = np.clip(np.round(128 + 60 * basis), 0, 255)
        y = flat.copy().astype(float)
        y[:8, :8] = blk
        p = encode_wmv1(y, chroma, chroma)
        bb = bits(p)
        print(f"  AC at (u={u},v={v}): {len(p)}B  bits[7:48]={bb[7:48]}")
