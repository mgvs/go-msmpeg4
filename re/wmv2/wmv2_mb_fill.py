"""wmv2_mb_fill.py — finish the WMV2 mb_non_intra inter tables for codes the single-MB encoder
never emits (a code is only emitted when its table is cheapest for that cbp). Multi-MB trick:
fill the frame with filler MBs whose cbp the TARGET table codes shortest, so the encoder picks
that table for the whole frame; then MB0 (the target cbp) is coded with the target-table code.

Reuses the parsing/anchor from wmv2_mb_extract (probe = MB0).
"""
import subprocess, json
import numpy as np
import wmv2_mb_extract as E

W, H, MBW, MBH, NMB = E.W, E.H, E.MBW, E.MBH, E.NMB
TEX = E.TEX
TMP = E.TMP


def perturb_mb(f1, n, cbp, checker, chroma=None):
    r0 = (n // MBW) * 16; c0 = (n % MBW) * 16
    if r0 + 18 > H or c0 + 18 > W:
        return False
    f1[r0:r0+16, c0:c0+16] = TEX[r0+2:r0+18, c0+2:c0+18]  # 2px shift -> inter MV=(4,4)
    for b in range(4):
        if (cbp >> (5 - b)) & 1:
            br = r0 + (b // 2) * 8; bc = c0 + (b % 2) * 8
            f1[br:br+8, bc:bc+8] = np.clip(f1[br:br+8, bc:bc+8] + checker, 0, 255)
    return True


def encode_multi(mb_cbp, q, mag=30):
    f0 = TEX.copy(); f1 = TEX.copy()
    ii, jj = np.indices((8, 8))
    checker = np.where((ii + jj) % 2 == 0, mag, -mag).astype(np.float64)
    cw, ch = W // 2, H // 2
    cb0 = E.smooth(202)[:ch, :cw].copy(); cr0 = E.smooth(303)[:ch, :cw].copy()
    cb1 = cb0.copy(); cr1 = cr0.copy()
    for n, cbp in mb_cbp.items():
        if not perturb_mb(f1, n, cbp, checker):
            continue
        r0 = (n // MBW) * 8; c0 = (n % MBW) * 8
        if r0 + 9 <= ch and c0 + 9 <= cw:
            cb1[r0:r0+8, c0:c0+8] = cb0[r0+1:r0+9, c0+1:c0+9]
            cr1[r0:r0+8, c0:c0+8] = cr0[r0+1:r0+9, c0+1:c0+9]
            for b in (4, 5):
                if (cbp >> (5 - b)) & 1:
                    pl = cb1 if b == 4 else cr1
                    pl[r0:r0+8, c0:c0+8] = np.clip(pl[r0:r0+8, c0:c0+8] + checker, 0, 255)
    raw = (f0.astype(np.uint8).tobytes() + cb0.astype(np.uint8).tobytes() + cr0.astype(np.uint8).tobytes() +
           f1.astype(np.uint8).tobytes() + cb1.astype(np.uint8).tobytes() + cr1.astype(np.uint8).tobytes())
    open(f"{TMP}/in.yuv", "wb").write(raw)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{W}x{H}", "-i", f"{TMP}/in.yuv", "-c:v", "wmv2", "-qscale:v", str(q),
                    "-frames:v", "2", "-g", "1000", "-bf", "0", "-sc_threshold", "1000000000",
                    "-me_range", "32", "-mbd", "0", f"{TMP}/c.avi"], check=True)
    sizes = [int(x) for x in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=size", "-of", "csv=p=0", f"{TMP}/c.avi"],
             capture_output=True, text=True).stdout.split()]
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-map", "0:v:0",
                           "-c", "copy", "-f", "data", "-"], capture_output=True).stdout
    bits = "".join(format(x, "08b") for x in data[sizes[0]:sizes[0]+sizes[1]])
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/c.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    y = np.frombuffer(out[fsz:fsz+W*H], np.uint8).reshape(H, W).astype(np.float64)
    cb = np.frombuffer(out[fsz+W*H:fsz+W*H+(W//2)*(H//2)], np.uint8).reshape(H//2, W//2).astype(np.float64)
    cr = np.frombuffer(out[fsz+W*H+(W//2)*(H//2):2*fsz], np.uint8).reshape(H//2, W//2).astype(np.float64)
    f0y = np.frombuffer(out[:W*H], np.uint8).reshape(H, W).astype(np.float64)  # decoded I
    return bits, y, cb, cr, f0y


def extract_forced(target_cbp, target_table, filler_cbp, q, mag=30):
    # MB0 = target; all other (edge-safe) MBs = filler -> forces target_table
    mb_cbp = {0: target_cbp}
    fi = 0
    for n in range(1, NMB):
        mb_cbp[n] = filler_cbp[fi % len(filler_cbp)]; fi += 1
    bits, y, cb, cr, f0y = encode_multi(mb_cbp, q, mag)
    ph = E.parse_header(bits)
    if ph is None:
        return None
    start, table, mv_idx, clean, st = ph
    if table != target_table or not clean:
        return None, table
    seg = bits[start:]
    for (mx, my) in [(4, 4), E.measure_mv(y, f0y)]:
        mvcode = E.MVCODE[mv_idx].get((mx, my))
        if mvcode is None:
            continue
        idx = seg.find(mvcode)
        if 1 <= idx <= 22:
            return target_table, seg[:idx], target_cbp
    return None, table


if __name__ == "__main__":
    import sys
    # gaps: table1 inter cbp 28,30,60 ; table0 too-short (find via re-verify)
    GAPS = {1: [28, 30, 60]}
    FILLERS = {0: [62, 52, 56, 61, 63], 1: [4, 8], 2: [0]}
    from collections import Counter
    for table, cbps in GAPS.items():
        for cbp in cbps:
            votes = Counter(); seen_tables = Counter()
            for q in [12, 14, 16, 18, 22, 24, 28]:
                for mag in (16, 24, 30):
                    r = extract_forced(cbp, table, FILLERS[table], q, mag)
                    if r is None:
                        continue
                    if r[0] is None:
                        seen_tables[r[1]] += 1
                        continue
                    votes[r[1]] += 1
            best = votes.most_common(1)
            print(f"table {table} cbp {cbp}: code={best[0][0] if best else None} (votes {dict(votes)}) seen_tables={dict(seen_tables)}")
