"""pframe_trace.py — VALIDATION-ONLY tracer. Decodes a P-frame's MB(0,0) fields using
the EXISTING go maps (mbNonIntraVLC, mvVLC0/1) parsed from our own .go files (never
ffmpeg source). Used only to ground-truth the bitstream structure while we build the
black-box re-derivation. Not part of the clean-room chain.
"""
import os
import re, sys, subprocess, os, random

PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_map(path, var):
    txt = open(path).read()
    m = re.search(rf'var {var} = func\(\) map\[string\]\[2\]int \{{.*?raw := map\[string\]\[2\]int\{{(.*?)\}}\s*return raw', txt, re.DOTALL)
    body = m.group(1)
    d = {}
    for bits, a, b in re.findall(r'"([01]+)":\s*\{(-?\d+),\s*(-?\d+)\}', body):
        d[bits] = (int(a), int(b))
    return d

MBNI = load_map(f"{PKG}/pframe_vlc.go", "mbNonIntraVLC")
MV0 = load_map(f"{PKG}/pframe_mv_vlc.go", "mvVLC0")
MV1 = load_map(f"{PKG}/pframe_mv_vlc.go", "mvVLC1")


class BR:
    def __init__(s, bits): s.b = bits; s.i = 0
    def bit(s): v = int(s.b[s.i]); s.i += 1; return v
    def u(s, n): v = int(s.b[s.i:s.i+n], 2); s.i += n; return v
    def vlc(s, tbl, maxlen):
        acc = ""
        for _ in range(maxlen):
            acc += s.b[s.i]; s.i += 1
            if acc in tbl: return tbl[acc], acc
        return None, acc


def trace(bs, nmb):
    r = BR(bs)
    pictype = r.u(2); quant = r.u(5); skip = r.bit()
    if r.b[r.i] == '0': rl = 0; r.i += 1
    else: rl = 1 + int(r.b[r.i+1]); r.i += 2
    dc = r.bit(); mv = r.bit()
    print(f"  hdr: pictype={pictype:02b} quant={quant} use_skip={skip} rl={rl} dc={dc} mv_idx={mv} -> hdr_len={r.i}")
    mvtab = MV1 if mv == 1 else MV0
    for n in range(min(nmb, 3)):
        start = r.i
        sk = r.bit() if skip else 0
        if sk == 1:
            print(f"  MB{n}: SKIP (bit@{start})")
            continue
        (val, code) = r.vlc(MBNI, 22)
        if val is None:
            print(f"  MB{n}: mb_type DECODE FAIL acc={code}"); return
        intra, cbp = val
        if not intra:
            (mvv, mvcode) = r.vlc(mvtab, 17)
            print(f"  MB{n}: not-skip@{start} mb_type@{start+skip}={code}->(inter,cbp={cbp}) MVcode@{start+skip+len(code)}={mvcode}->dmv={mvv}")
            if cbp != 0:
                print(f"        cbp!=0 -> residual blocks present; stopping detailed trace")
                return
        else:
            print(f"  MB{n}: not-skip mb_type={code}->(INTRA,cbp={cbp})")
            return


# rebuild the same clean clips as the analyzer
W, H = 96, 80
NMB = (W//16)*(H//16)
random.seed(1234)
Y0 = bytes(random.randrange(16,240) for _ in range(W*H))
CHROMA = bytes([128])*((W//2)*(H//2))

def shift(plane, dx, dy):
    out = bytearray(W*H)
    for rr in range(H):
        sr = min(max(rr-dy,0),H-1)
        for c in range(W):
            sc = min(max(c-dx,0),W-1)
            out[rr*W+c] = plane[sr*W+sc]
    return bytes(out)

def pbits(dx,dy):
    raw = Y0+CHROMA+CHROMA+shift(Y0,dx,dy)+CHROMA+CHROMA
    open("/tmp/pf_mv/in.yuv","wb").write(raw)
    subprocess.run(["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","yuv420p","-s",f"{W}x{H}","-i","/tmp/pf_mv/in.yuv","-c:v","msmpeg4","-qscale:v","4","-frames:v","2","-g","1000","-bf","0","-sc_threshold","1000000000","-me_range","64","-vtag","DIV3","/tmp/pf_mv/c.avi"],check=True)
    sizes=[int(x) for x in subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","packet=size","-of","csv=p=0","/tmp/pf_mv/c.avi"],capture_output=True,text=True).stdout.split()]
    data=subprocess.run(["ffmpeg","-v","error","-i","/tmp/pf_mv/c.avi","-map","0:v:0","-c","copy","-f","data","-"],capture_output=True).stdout
    p=data[sizes[0]:sizes[0]+sizes[1]]
    return "".join(format(x,"08b") for x in p)

os.makedirs("/tmp/pf_mv",exist_ok=True)
for (dx,dy) in [(1,0),(2,0),(0,1),(0,2),(2,0)]:
    bs=pbits(dx,dy)
    print(f"clip dx={dx} dy={dy} ({len(bs)}b): expect MB0 dmv=(2*dx,2*dy)=({2*dx},{2*dy})")
    trace(bs,NMB)
