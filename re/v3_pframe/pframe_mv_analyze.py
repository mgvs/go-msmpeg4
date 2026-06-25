"""pframe_mv_analyze.py — structurally locate the MV field in a global-shift P-frame
and extract the MV codeword, fully black-box (ffmpeg binary only).

Model:  P = [header | skip0 | mb_type(inter,cbp0) | MV-code | skip1*(N-1) | pad-ones]
All inter-cbp0 clips share an identical prefix up to the MV field (O). The MV code
ends where a long run of '1' (the skip tail, >= N-1 bits) begins.

We DO NOT read any VLC table to extract the codeword. For a sanity cross-check only,
we optionally compare against the existing (to-be-replaced) mvVLC tables.
"""
import sys, subprocess, os, random

W, H = 96, 80          # 6x5 = 30 MBs -> skip tail >= 29 ones (MV codes <= 17 bits)
NMB = (W // 16) * (H // 16)
TMP = "/tmp/pf_mv"
os.makedirs(TMP, exist_ok=True)


def make_noise(seed=1234):
    random.seed(seed)
    return bytes(random.randrange(16, 240) for _ in range(W * H))


def shift_plane(plane, dx, dy):
    out = bytearray(W * H)
    for r in range(H):
        sr = min(max(r - dy, 0), H - 1)
        base = r * W
        sbase = sr * W
        for col in range(W):
            sc = min(max(col - dx, 0), W - 1)
            out[base + col] = plane[sbase + sc]
    return bytes(out)


Y0 = make_noise()
CHROMA = bytes([128]) * ((W // 2) * (H // 2))


def pframe_bits(dx, dy):
    y1 = shift_plane(Y0, dx, dy)
    raw = Y0 + CHROMA + CHROMA + y1 + CHROMA + CHROMA
    inp = f"{TMP}/in.yuv"
    open(inp, "wb").write(raw)
    avi = f"{TMP}/clip.avi"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{W}x{H}", "-i", inp,
        "-c:v", "msmpeg4", "-qscale:v", "4", "-frames:v", "2",
        "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
        "-me_range", "64", "-vtag", "DIV3", avi,
    ], check=True)
    # second video packet = P-frame
    sizes = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=size", "-of", "csv=p=0", avi,
    ], capture_output=True, text=True).stdout.split()
    sizes = [int(s) for s in sizes]
    data = subprocess.run([
        "ffmpeg", "-v", "error", "-i", avi, "-map", "0:v:0", "-c", "copy", "-f", "data", "-",
    ], capture_output=True).stdout
    p = data[sizes[0]:sizes[0] + sizes[1]]
    return "".join(format(x, "08b") for x in p)


def parse_header(bs):
    i = 0
    pictype = bs[i:i+2]; i += 2
    quant = int(bs[i:i+5], 2); i += 5
    skip = bs[i]; i += 1
    # c3 VLC: 0->0, 10->1, 11->2
    if bs[i] == '0':
        rl = 0; i += 1
    else:
        rl = 1 + int(bs[i+1]); i += 2
    dc = bs[i]; i += 1
    mv = bs[i]; i += 1
    return dict(pictype=pictype, quant=quant, use_skip=skip, rl=rl, dc=dc, mv=int(mv), hdr_len=i)


def skip_tail_start(bs):
    """index where the maximal trailing run of '1' (>= NMB-1) begins."""
    j = len(bs)
    while j > 0 and bs[j-1] == '1':
        j -= 1
    return j  # first index of the trailing ones run


if __name__ == "__main__":
    # Probe a spread of shifts; some codes start with 0, some with 1 -> LCP collapses to O.
    shifts = [(1,0),(2,0),(3,0),(0,1),(0,2),(1,1),(2,1),(-1,0),(0,-1),(2,2),(3,1),(1,3)]
    clips = {}
    for (dx,dy) in shifts:
        bs = pframe_bits(dx,dy)
        clips[(dx,dy)] = bs
    # header from first clip
    h = parse_header(clips[shifts[0]])
    print("header:", h)
    # longest common prefix across all clips
    ref = clips[shifts[0]]
    lcp = len(ref)
    for bs in clips.values():
        k = 0
        while k < min(lcp, len(bs)) and bs[k] == ref[k]:
            k += 1
        lcp = min(lcp, k)
    print("LCP across clips (>= MV-start O):", lcp)
    for (dx,dy), bs in clips.items():
        end = skip_tail_start(bs)
        seg = bs[lcp:end]
        print(f"dx={dx:>2} dy={dy:>2}  tail@{end:>3}  prefix[{lcp}:]={bs[lcp:lcp+1]}  MVseg({len(seg)})={seg}")
