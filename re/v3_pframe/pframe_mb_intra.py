"""pframe_mb_intra.py — black-box extraction of table_mb_non_intra (intra half).

Encoder-oracle, no ffmpeg source. Reference is flat 128 everywhere, so every MB except
the probe is a perfect match and encodes as SKIP. The probe MB (first coded MB) is filled
with texture in the chosen blocks -> the encoder cannot predict it from the flat reference
and codes it as an INTRA MB; the textured blocks become AC-coded (cbp bit set), flat
blocks carry only DC (cbp bit clear).

  P = [hdr] [skip*8] [mb_type(intra,cbp)] [ac_pred(1)] [per-block DC (+AC if coded)] ...

To find where mb_type ends we encode two clips with the SAME coded-block layout but
DIFFERENT texture values; their bitstreams are identical through mb_type (+ac_pred) and
diverge at the first DC codeword. The first-diff position therefore bounds mb_type; the
1-bit ac_pred ambiguity is resolved by requiring the codeword to be disjoint from the
known inter prefix tree. cbp is read from the decoded pixels.
"""
import os
import subprocess, os, json, random
import numpy as np

W, H = 112, 96
MBW, MBH = W // 16, H // 16
NMB = MBW * MBH
PROBE = MBW + 1
PR_R, PR_C = 16, 16
HDR, SKIPS = 12, PROBE
TMP = "/tmp/pf_mbi"
os.makedirs(TMP, exist_ok=True)
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def encode_intra(coded_luma, coded_chroma, blk0_base):
    # blocks 1..5 use a FIXED seed (identical across the two clips); only block0's DC
    # level (blk0_base) differs between clips -> the bitstream diverges exactly at
    # block0's DC codeword, which sits right after mb_type + ac_pred(1).
    rng = random.Random(4242)
    f0 = np.full((H, W), 128.0)
    f1 = np.full((H, W), 128.0)
    rng0 = random.Random(0x5a5a)
    for i in range(16):
        for j in range(16):
            f0[PR_R + i, PR_C + j] = rng0.randrange(20, 236)   # kill inter prediction
    # block0 is the anchor: flat at blk0_base (+ zero-mean AC if it is a coded block)
    b0coded = 0 in coded_luma
    for i in range(8):
        for j in range(8):
            v = blk0_base + ((20 if (i + j) % 2 else -20) if b0coded else 0)
            f1[PR_R + i, PR_C + j] = min(max(v, 0), 255)
    for blk in coded_luma:
        if blk == 0:
            continue
        br = PR_R + (blk // 2) * 8; bc = PR_C + (blk % 2) * 8
        for i in range(8):
            for j in range(8):
                f1[br + i, bc + j] = rng.randrange(20, 236)
    cw, ch = W // 2, H // 2
    cb0 = np.full((ch, cw), 128.0); cr0 = np.full((ch, cw), 128.0)
    cb1 = np.full((ch, cw), 128.0); cr1 = np.full((ch, cw), 128.0)
    for blk in coded_chroma:
        pl = cb1 if blk == 4 else cr1
        for i in range(8):
            for j in range(8):
                pl[PR_R // 2 + i, PR_C // 2 + j] = rng.randrange(20, 236)
    raw = (f0.astype(np.uint8).tobytes() + cb0.astype(np.uint8).tobytes() + cr0.astype(np.uint8).tobytes() +
           f1.astype(np.uint8).tobytes() + cb1.astype(np.uint8).tobytes() + cr1.astype(np.uint8).tobytes())
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "msmpeg4", "-qscale:v", "4",
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-mbd", "0", "-vtag", "DIV3", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    bits = "".join(format(x, "08b") for x in data[sizes[0]:sizes[0]+sizes[1]])
    # decode P frame to read cbp from pixels
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    p = out[fsz:2*fsz]
    y = np.frombuffer(p[:W*H], np.uint8).reshape(H, W).astype(np.float64)
    cb = np.frombuffer(p[W*H:W*H+(W//2)*(H//2)], np.uint8).reshape(H//2, W//2).astype(np.float64)
    cr = np.frombuffer(p[W*H+(W//2)*(H//2):fsz], np.uint8).reshape(H//2, W//2).astype(np.float64)
    return bits, y, cb, cr


def read_cbp(y, cb, cr):
    coded = []
    for blk in range(4):
        br = PR_R + (blk // 2) * 8; bc = PR_C + (blk % 2) * 8
        coded.append(1 if y[br:br+8, bc:bc+8].var() > 8 else 0)
    coded.append(1 if cb[PR_R//2:PR_R//2+8, PR_C//2:PR_C//2+8].var() > 8 else 0)
    coded.append(1 if cr[PR_R//2:PR_R//2+8, PR_C//2:PR_C//2+8].var() > 8 else 0)
    return sum(coded[b] << (5 - b) for b in range(6))


def load_existing():
    import re
    txt = open(f"{PKG}/pframe_vlc.go").read()
    m = re.search(r'var mbNonIntraVLC = .*?raw := map\[string\]\[2\]int\{(.*?)\}\s*return raw', txt, re.DOTALL)
    d = {}
    for bits, a, b in re.findall(r'"([01]+)":\s*\{(-?\d+),\s*(-?\d+)\}', m.group(1)):
        d[bits] = (int(a), int(b))
    return d


INTER = json.load(open("/tmp/pf_mbx/mb_inter.json"))   # code -> [0,cbp]
INTER_CODES = set(INTER.keys())


def is_inter_prefix(s):
    return any(c.startswith(s) for c in INTER_CODES)


def prefix_free_vs(code, others):
    if code in others:
        return False
    return not any(o.startswith(code) or code.startswith(o) for o in others)


def extract_intra(cbp, known):
    cl = [b for b in range(4) if (cbp >> (5 - b)) & 1]
    cc = [b for b in (4, 5) if (cbp >> (5 - b)) & 1]
    start = HDR + SKIPS + 1
    acbp = None
    cands = []
    for ba, bb in [(128, 40), (128, 210), (70, 200), (100, 175), (50, 230)]:
        b1, y, cb, cr = encode_intra(cl, cc, blk0_base=ba)
        b2, _, _, _ = encode_intra(cl, cc, blk0_base=bb)
        if acbp is None:
            acbp = read_cbp(y, cb, cr)
        s1, s2 = b1[start:], b2[start:]
        d = 0
        while d < min(len(s1), len(s2)) and s1[d] == s2[d]:
            d += 1
        for mbt in (s1[:d - 1], s1[:d]):       # ac_pred may or may not be the diverging bit
            if 1 <= len(mbt) <= 21:
                cands.append(mbt)
    # pick the shortest candidate that stays prefix-free vs inter codes and known intra
    others = INTER_CODES | known
    valid = [c for c in cands if prefix_free_vs(c, others)]
    if not valid:
        return None, acbp
    valid.sort(key=len)
    return valid[0], acbp


if __name__ == "__main__":
    EX = load_existing()
    found = {}
    codeset = set()
    for cbp in range(64):
        code, acbp = extract_intra(cbp, codeset)
        if code is None:
            print(f"  cbp={cbp}: no candidate")
            continue
        if acbp not in found:
            found[acbp] = code
            codeset.add(code)
    print(f"intra: {len(found)}/64 cbp covered")
    ok = bad = 0
    for cbp, code in sorted(found.items()):
        ex = EX.get(code)
        if ex == (1, cbp): ok += 1
        else:
            bad += 1
            print(f"  cbp={cbp:2d} code={code:>20} -> existing={ex}")
    print(f"verify vs existing: ok={ok} bad={bad}")
    json.dump({code: [1, cbp] for cbp, code in found.items()}, open(f"{TMP}/mb_intra.json", "w"))
