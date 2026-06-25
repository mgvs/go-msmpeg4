"""iframe_mcbpc.py — black-box extraction of the v3 I-frame joint MCBPC/CBPY VLC
(table_mb_intra -> mcbpc_table.go). Encoder-oracle, no ffmpeg source.

A single 16x16 macroblock I-frame is encoded; MB(0,0) is the only MB, all neighbours
absent, so CBP prediction is 0 and the table's raw (cb,cr,y0..y3) equals the actually
coded blocks. We texture the blocks we want coded (AC -> cbp bit set) and read the actual
pattern from the decoded pixels. The MCBPC codeword sits right after the picture header;
two clips that differ only in block-0's DC level diverge exactly at block-0's DC codeword,
which follows mb_type(MCBPC) + ac_pred(1), so MCBPC = bits before ac_pred.
"""
import os
import subprocess, os, json, random, re
import numpy as np

W, H = 16, 16
TMP = "/tmp/iframe_mcbpc"
os.makedirs(TMP, exist_ok=True)
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def encode_iframe(coded_luma, coded_chroma, blk0_base):
    rng = random.Random(4242)
    y = np.full((H, W), 128.0)
    # block0 = anchor: flat blk0_base (+ zero-mean AC if it is a coded block)
    b0coded = 0 in coded_luma
    for i in range(8):
        for j in range(8):
            y[i, j] = min(max(blk0_base + ((20 if (i + j) % 2 else -20) if b0coded else 0), 0), 255)
    for blk in coded_luma:
        if blk == 0:
            continue
        br, bc = (blk // 2) * 8, (blk % 2) * 8
        for i in range(8):
            for j in range(8):
                y[br + i, bc + j] = rng.randrange(20, 236)
    cb = np.full((H // 2, W // 2), 128.0)
    cr = np.full((H // 2, W // 2), 128.0)
    for blk in coded_chroma:
        pl = cb if blk == 4 else cr
        for i in range(8):
            for j in range(8):
                pl[i, j] = rng.randrange(20, 236)
    raw = y.astype(np.uint8).tobytes() + cb.astype(np.uint8).tobytes() + cr.astype(np.uint8).tobytes()
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "msmpeg4", "-qscale:v", "4",
                    "-frames:v", "1", "-vtag", "DIV3", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    bits = "".join(format(x, "08b") for x in data[:sizes[0]])
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    dy = np.frombuffer(out[:W*H], np.uint8).reshape(H, W).astype(np.float64)
    dcb = np.frombuffer(out[W*H:W*H+(W//2)*(H//2)], np.uint8).reshape(H//2, W//2).astype(np.float64)
    dcr = np.frombuffer(out[W*H+(W//2)*(H//2):fsz], np.uint8).reshape(H//2, W//2).astype(np.float64)
    return bits, dy, dcb, dcr


def header_len(bits):
    i = 2 + 5 + 5
    for _ in range(2):  # two c3 fields
        if bits[i] == '0':
            i += 1
        else:
            i += 2
    i += 1              # dc_table_index
    return i


def read_pattern(y, cb, cr):
    # Read actually-coded blocks from pixels, then undo intra CBP prediction to get the
    # RAW table value (even at MB(0,0) blocks 1..3 are predicted from block0 within the MB).
    a = []
    for blk in range(4):
        br, bc = (blk // 2) * 8, (blk % 2) * 8
        a.append(1 if y[br:br+8, bc:bc+8].var() > 8 else 0)
    cbc = 1 if cb[:8, :8].var() > 8 else 0
    crc = 1 if cr[:8, :8].var() > 8 else 0
    a0, a1, a2, a3 = a
    r0 = a0
    r1 = a1 ^ a0
    r2 = a2 ^ a0
    pred3 = a2 if a0 == a1 else a1
    r3 = a3 ^ pred3
    return (cbc, crc, r0, r1, r2, r3)


def load_existing():
    txt = open(f"{PKG}/mcbpc_table.go").read()
    d = {}
    for ln, code, cb, cr, y0, y1, y2, y3 in re.findall(
            r'\{(\d+),\s*0b([01]+),\s*(\d),\s*(\d),\s*(\d),\s*(\d),\s*(\d),\s*(\d)\}', txt):
        bits = code  # already binary text after 0b
        d[(int(cb), int(cr), int(y0), int(y1), int(y2), int(y3))] = bits
    return d


def prefix_free(code, others):
    return code not in others and not any(o.startswith(code) or code.startswith(o) for o in others)


def extract(pattern_blocks, known):
    cl = [b for b in range(4) if pattern_blocks[2 + b]]   # y0..y3
    cc = [4] if pattern_blocks[0] else []
    if pattern_blocks[1]:
        cc.append(5)
    act = None
    cands = []
    for ba, bb in [(128, 40), (128, 210), (70, 200), (100, 175), (50, 230)]:
        b1, y, cb, cr = encode_iframe(cl, cc, ba)
        b2, _, _, _ = encode_iframe(cl, cc, bb)
        if act is None:
            act = read_pattern(y, cb, cr)
        h = header_len(b1)
        s1, s2 = b1[h:], b2[h:]
        d = 0
        while d < min(len(s1), len(s2)) and s1[d] == s2[d]:
            d += 1
        for mbt in (s1[:d - 1], s1[:d]):
            if 1 <= len(mbt) <= 14:
                cands.append(mbt)
    valid = [c for c in cands if prefix_free(c, known)]
    if not valid:
        return None, act
    valid.sort(key=len)
    return valid[0], act


if __name__ == "__main__":
    EX = load_existing()
    print(f"existing mcbpcVLC: {len(EX)} patterns")
    found = {}
    codeset = set()
    for pat in range(64):
        # pat bits: cb=bit5,cr=bit4,y0=bit3,y1=bit2,y2=bit1,y3=bit0  (arbitrary enumeration)
        pattern = ((pat >> 5) & 1, (pat >> 4) & 1, (pat >> 3) & 1, (pat >> 2) & 1, (pat >> 1) & 1, pat & 1)
        code, act = extract(pattern, codeset)
        if code is None:
            continue
        if act not in found:
            found[act] = code
            codeset.add(code)
    print(f"covered {len(found)}/64 patterns")
    ok = bad = 0
    for pat, code in sorted(found.items()):
        ex = EX.get(pat)
        if ex == code:
            ok += 1
        else:
            bad += 1
            print(f"  pattern(cb,cr,y0-3)={pat} code={code} existing={ex}")
    print(f"verify vs existing mcbpc_table.go: ok={ok} bad={bad}")
    json.dump({f"{p[0]}{p[1]}_{p[2]}{p[3]}{p[4]}{p[5]}": c for p, c in found.items()},
              open(f"{TMP}/table_mb_intra.json", "w"))
