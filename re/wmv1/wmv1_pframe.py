"""wmv1_pframe.py — probe the WMV1 P-frame structure and derive the inter scan table.

WMV1 P-frame header (after the I-frame set bit_rate via its ext-header):
  pictype(2)=01 | qscale(5) | use_skip_mb_code(1) | [per_mb_rl_table(1) if bit_rate>MBAC]
  | rl_table c3 | dc_table(1) | mv_table(1)
Then per-MB: [skip(1) if use_skip] | mb_type(table_mb_non_intra) | (inter) MV | coded-block AC.
The VLC tables (mb_type, MV, RL) are shared with v3. Inter AC uses the WMV1 ESC3 + a different
(inter) scan table — which we derive here by the decoder-oracle.
"""
import subprocess, os, random
import numpy as np

W, H = 64, 64
Q = 4
TMP = "/tmp/wmv1p"
os.makedirs(TMP, exist_ok=True)


def encode_clip(frame0, frame1):
    cw, ch = W // 2, H // 2
    flatc = bytes([128]) * (cw * ch)
    raw = (frame0.astype(np.uint8).tobytes() + flatc + flatc +
           frame1.astype(np.uint8).tobytes() + flatc + flatc)
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "wmv1", "-qscale:v", str(Q),
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    pkts = []
    off = 0
    for s in sizes:
        pkts.append(data[off:off+s]); off += s
    return pkts


def bits(b):
    return "".join(format(x, "08b") for x in b)


if __name__ == "__main__":
    rng = random.Random(7)
    f0 = np.array([rng.randrange(30, 220) for _ in range(W*H)], np.float64).reshape(H, W)
    # P-frame identical to I -> all-skip P-frame (smallest), exposes the header
    pkts = encode_clip(f0, f0.copy())
    print(f"{len(pkts)} packets, sizes={[len(p) for p in pkts]}")
    pb = bits(pkts[1])
    print(f"P-frame {len(pkts[1])}B, first 40 bits: {pb[:40]}")
    # parse header
    i = 0
    pictype = pb[i:i+2]; i += 2
    q = int(pb[i:i+5], 2); i += 5
    skip = pb[i]; i += 1
    print(f"  pictype={pictype} q={q} use_skip={skip}  bits[i:]={pb[i:i+20]}")
