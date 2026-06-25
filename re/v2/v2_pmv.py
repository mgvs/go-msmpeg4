"""v2_pmv.py — reverse the H.263 MV VLC and the constant v2 P mb_type prefix, black-box.

Global-shift P-frame: a uniform integer shift makes MB(0,0) the only coded MB (its predictor
is 0 -> dmv = shift; all later MBs have dmv=0 -> skip). So
  P = [hdr(8)] [skip0] [mb_type(inter,cbp0)] [cbpy(0)] [MVx-code] [MVy-code(=0)] [skip*…]
The bits before the MV codes are constant across shifts; first-diff isolates the MVx code, and
the H.263 MV code value equals |MVD| = 2*|dx| (half-pel units, f_code=1). Half-pel shifts give
the odd code values.
"""
import subprocess, os, random
import numpy as np

W, H = 64, 48
Q = 4
TMP = "/tmp/v2p"
os.makedirs(TMP, exist_ok=True)
def _smooth(seed):
    rng = random.Random(seed)
    lw, lh = W//8+2, H//8+2
    low = np.array([[rng.randrange(40, 216) for _ in range(lw)] for _ in range(lh)], np.float64)
    yi = np.linspace(0, lh-1.001, H); xi = np.linspace(0, lw-1.001, W)
    y0 = np.floor(yi).astype(int); x0 = np.floor(xi).astype(int)
    fy = (yi-y0)[:, None]; fx = (xi-x0)[None, :]
    a = low[y0][:, x0]; b = low[y0][:, x0+1]; c = low[y0+1][:, x0]; d = low[y0+1][:, x0+1]
    return np.clip(np.round(a*(1-fy)*(1-fx)+b*(1-fy)*fx+c*fy*(1-fx)+d*fy*fx), 0, 255)

BASE = _smooth(11)


def shifted(dx2, dy2):
    dx, dy = dx2/2.0, dy2/2.0
    rr = np.arange(H)[:, None]-dy
    cc = np.arange(W)[None, :]-dx
    r0 = np.clip(np.floor(rr).astype(int), 0, H-1); c0 = np.clip(np.floor(cc).astype(int), 0, W-1)
    r1 = np.clip(r0+1, 0, H-1); c1 = np.clip(c0+1, 0, W-1)
    fr = np.clip(rr-np.floor(rr), 0, 1); fc = np.clip(cc-np.floor(cc), 0, 1)
    out = (BASE[r0, c0]*(1-fr)*(1-fc)+BASE[r0, c1]*(1-fr)*fc+BASE[r1, c0]*fr*(1-fc)+BASE[r1, c1]*fr*fc)
    return np.clip(np.round(out), 0, 255)


def pbits(dx2, dy2):
    cw, ch = W//2, H//2
    flatc = bytes([128])*(cw*ch)
    raw = BASE.astype(np.uint8).tobytes()+flatc+flatc + shifted(dx2, dy2).astype(np.uint8).tobytes()+flatc+flatc
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "msmpeg4v2", "-qscale:v", str(Q),
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-me_range", "32", "-vtag", "MP42", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    p = data[sizes[0]:sizes[0]+sizes[1]]
    return "".join(format(x, "08b") for x in p)


def tail_skip_start(b):
    j = len(b)
    while j > 0 and b[j-1] == '1':
        j -= 1
    return j


def lcp(strs):
    p = strs[0]
    for s in strs[1:]:
        k = 0
        while k < min(len(p), len(s)) and p[k] == s[k]:
            k += 1
        p = p[:k]
    return p


def lcs(strs):  # longest common suffix
    return lcp([s[::-1] for s in strs])[::-1]


if __name__ == "__main__":
    # x-shifts -> MVD_x even; strip trailing pad ('1's) so the common suffix is MVy(0)+skips.
    segs = {}
    for dx in list(range(-8, 0)) + list(range(1, 9)):
        b = pbits(2*dx, 0)[9:]            # after hdr(8)+MB0 skip(1)
        segs[dx] = b.rstrip("1") or b     # drop byte-pad ones (skips are also 1, but suffix LCS handles it)
    P = lcp(list(segs.values()))
    S = lcs(list(segs.values()))
    print(f"prefix P (mb_type+cbpy) = '{P}' ({len(P)})")
    print(f"suffix S (MVy0+skips)   = '{S}' ({len(S)})")
    mag = {}   # |MVD| -> {dx>0 code, dx<0 code}
    for dx in sorted(segs):
        mvx = segs[dx][len(P):len(segs[dx])-len(S)]
        mag.setdefault(abs(2*dx), {})[1 if dx > 0 else -1] = mvx
        print(f"  dx={dx:>3} MVD={2*dx:>3}: MVx='{mvx}'")
    print("\n|MVD| -> magnitude VLC (common of +/-), sign bit:")
    for v in sorted(mag):
        if 1 in mag[v] and -1 in mag[v]:
            m = lcp([mag[v][1], mag[v][-1]])
            print(f"  {v:>3}: mag='{m}'  (+ ='{mag[v][1]}', - ='{mag[v][-1]}')")
