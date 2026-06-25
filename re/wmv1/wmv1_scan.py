"""wmv1_scan.py — derive the WMV1 (WMV7) scan tables black-box (ffmpeg encoder only).

WMV1 reuses the v3 RL-VLC tables but a different scan. We encode a 16x16 intra frame whose
luma block-0 holds exactly one AC coefficient at DCT position (u,v) (zero-mean basis -> DC
unchanged), everything else flat. Across all (u,v) the bits up to the AC code are constant
(same header / MCBPC / ac_pred / DC=0), so the longest common prefix pins the AC-code start
O; the AC code at bits[O:] is decoded with our (v3) luma RL table -> run -> scan[run+1]=(u,v).

intra zig-zag uses ac_pred=0 (first MB, no neighbours). alt-H/alt-V need ac_pred=1 (later).
"""
import os
import subprocess, os, re
import numpy as np

W, H = 16, 16
Q = 4
TMP = "/tmp/wmv1"
os.makedirs(TMP, exist_ok=True)
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

M = np.array([[0.5*(1/np.sqrt(2) if k == 0 else 1)*np.cos((2*n+1)*k*np.pi/16) for n in range(8)] for k in range(8)])
CHROMA = np.full((H//2, W//2), 128, np.uint8)


def load_rl(varname):
    for f in ("tcoef_table.go", "tcoef_tables_extra.go"):
        txt = open(f"{PKG}/{f}").read()
        m = re.search(rf'var {varname} = \[\]tcoefCode\{{(.*?)\n\}}', txt, re.DOTALL)
        if not m:
            continue
        d = {}
        # support both positional {r,l,la,len,0bcode} and named {run:..}
        for r, l, la, ln, code in re.findall(r'\{\s*(?:run:\s*)?(\d+),\s*(?:level:\s*)?(\d+),\s*(?:last:\s*)?(\d+),\s*(?:length:\s*)?(\d+),\s*(?:code:\s*)?0b([01]+)\s*\}', m.group(1)):
            d[code.zfill(int(ln))] = (int(r), int(l), int(la))
        return d
    raise KeyError(varname)

LUMA = {0: load_rl("tcoefTable0VLC"), 1: load_rl("tcoefTable2VLC"), 2: load_rl("tcoefLumaVLC")}


def encode(y):
    raw = y.astype(np.uint8).tobytes() + CHROMA.tobytes() + CHROMA.tobytes()
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                    "-s", f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "wmv1", "-qscale:v", str(Q),
                    "-frames:v", "1", f"{TMP}/c.avi"], check=True)
    sz = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
          "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
          capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    return "".join(format(x, "08b") for x in data[:sz[0]])


def coeff_frame(u, v, amp=48):
    basis = M.T[:, u][:, None] @ M[v, :][None, :]
    y = np.full((H, W), 128.0)
    y[:8, :8] = np.clip(128 + amp * basis, 0, 255)
    return encode(y)


def rl_decode_first(bitstr, table):
    acc = ""
    for ch in bitstr[:24]:
        acc += ch
        if acc in table:
            return table[acc]
    return None


if __name__ == "__main__":
    positions = [(u, v) for u in range(8) for v in range(8) if (u, v) != (0, 0)]
    frames = {}
    for (u, v) in positions:
        frames[(u, v)] = coeff_frame(u, v)
    # longest common prefix across all single-coeff frames -> AC-code start O
    fl = list(frames.values())
    O = len(fl[0])
    for b in fl[1:]:
        k = 0
        while k < min(O, len(b)) and b[k] == fl[0][k]:
            k += 1
        O = min(O, k)
    print(f"AC-code start O = {O};  header+MCBPC+DC prefix = {fl[0][:O]}")
    # pick the RL table index that decodes the most frames cleanly
    best_idx, best_ok = 2, -1
    for idx in (0, 1, 2):
        ok = sum(1 for b in fl if rl_decode_first(b[O:], LUMA[idx]) is not None)
        if ok > best_ok:
            best_ok, best_idx = ok, idx
    print(f"using luma RL table index {best_idx} ({best_ok}/{len(fl)} decode)")
    tbl = LUMA[best_idx]
    scan = {0: (0, 0)}
    runs = {}
    for (u, v) in positions:
        r = rl_decode_first(frames[(u, v)][O:], tbl)
        if r is None:
            continue
        run, level, last = r
        runs[(u, v)] = (run, level, last)
        scan[run + 1] = (u, v)
    print(f"recovered {len(scan)}/64 scan positions; covers all = {len(set(scan.values()))==64}")
    # print the scan as zigZagUV-style [64][2]
    order = [scan.get(k) for k in range(64)]
    miss = [k for k in range(64) if order[k] is None]
    print(f"missing scan indices: {miss}")
    print("scan (k -> u,v):")
    for k in range(64):
        print(f"  {k:2d}: {order[k]}")
