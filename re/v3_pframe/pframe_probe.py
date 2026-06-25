"""pframe_probe.py — validate the P-frame bitstream model for clean-room MV reversal.

Black-box ONLY: ffmpeg binary is used as encoder (controlled YUV -> DIV3 bits).
No ffmpeg source is read. Builds a 2-frame clip [I = random noise, P = noise shifted
by an integer (dx,dy)] and dumps the P-frame bitstream so we can confirm the layout:

    header | skip=0 | mb_type(inter,cbp=0) | MV-code(V) | skip=1 * (N-1) | pad-ones

Usage: python3 pframe_probe.py <dx> <dy>
"""
import sys, subprocess, struct, os, random

W, H = 64, 48          # 4x3 = 12 MBs ... small; we want many MBs for a long skip tail
W, H = 96, 80          # 6x5 = 30 MBs -> 29-bit ones tail >> 17-bit max MV code
TMP = "/tmp/pf_probe"
os.makedirs(TMP, exist_ok=True)


def make_noise(seed):
    random.seed(seed)
    n = W * H
    c = (W // 2) * (H // 2)
    y = bytes(random.randrange(16, 240) for _ in range(n))
    cb = bytes([128]) * c
    cr = bytes([128]) * c
    return y, cb, cr


def shift_plane(plane, w, h, dx, dy):
    """Integer shift with clamped edges (so a block that moves by (dx,dy) matches exactly)."""
    out = bytearray(w * h)
    for r in range(h):
        sr = min(max(r - dy, 0), h - 1)
        for col in range(w):
            sc = min(max(col - dx, 0), w - 1)
            out[r * w + col] = plane[sr * w + sc]
    return bytes(out)


def encode_clip(dx, dy):
    y0, cb0, cr0 = make_noise(1234)
    y1 = shift_plane(y0, W, H, dx, dy)
    # chroma flat -> unchanged
    raw = y0 + cb0 + cr0 + y1 + cb0 + cr0
    inp = f"{TMP}/in_{dx}_{dy}.yuv"
    open(inp, "wb").write(raw)
    avi = f"{TMP}/clip_{dx}_{dy}.avi"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}", "-i", inp,
        "-c:v", "msmpeg4", "-qscale:v", "4", "-frames:v", "2",
        "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
        "-me_range", "64", "-vtag", "DIV3", avi,
    ], check=True)
    return avi


def frame_packets(avi):
    """Return list of (bytes) for each video packet, via ffprobe sizes + raw data dump."""
    out = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=size,flags", "-of", "csv=p=0", avi,
    ], capture_output=True, text=True).stdout.strip().splitlines()
    sizes = []
    for line in out:
        parts = line.split(",")
        sizes.append((int(parts[0]), parts[1] if len(parts) > 1 else ""))
    data = subprocess.run([
        "ffmpeg", "-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-",
    ], capture_output=True).stdout
    pkts = []
    off = 0
    for sz, fl in sizes:
        pkts.append((data[off:off + sz], fl))
        off += sz
    return pkts


def bits(b):
    return "".join(format(x, "08b") for x in b)


if __name__ == "__main__":
    dx = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    dy = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    avi = encode_clip(dx, dy)
    pkts = frame_packets(avi)
    print(f"clip dx={dx} dy={dy}: {len(pkts)} packets, sizes={[len(p) for p,_ in pkts]}, flags={[f for _,f in pkts]}")
    for i, (p, fl) in enumerate(pkts):
        bs = bits(p)
        print(f"--- frame {i} flags={fl} len={len(p)}B {len(bs)}b ---")
        print(bs[:120])
