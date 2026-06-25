"""wmv2_mb_verify.py — reliable decoder-verify by REAL-TAIL TRANSPLANT, to assign the
Kraft-determined free-leaf codes to their cbp (finishing the WMV2 mb_non_intra tables).

For a candidate code L (a free leaf of table T) and a target cbp C: take a real encoded frame whose
MB0 has cbp C (so the bits after its mb_type are a VALID MV+AC tail for C), and splice
  [header with cbp_index forcing table T][L][real tail for C].
If L decodes (in table T) to cbp C, the tail aligns and MB0 reconstructs as the clean cbp-C content
(real AC -> no overflow -> stable cbp read). If L decodes to a different cbp, the tail misaligns and
MB0 is garbage. So L -> C is the C whose tail gives a clean MB0 matching cbp C.
"""
import subprocess, json
import numpy as np
import wmv2_mb_extract as E

W, H, TMP = E.W, E.H, E.TMP
CBP_MAP = E.CBP_MAP


def cbpidx_for_table(q, table):
    row = (q > 10) + (q > 20)
    for ci in (0, 1, 2):
        if CBP_MAP[row][ci] == table:
            return {0: "0", 1: "10", 2: "11"}[ci]
    return None


def real_tail(cbp, q):
    """Encode a real frame with MB0=cbp; return (prefix_bits_up_to_cbpidx, after_cbpidx_to_start,
    K=mb_type, tail=MV+AC+rest, host_avi info, decoded cbp-C planes for comparison)."""
    pl = [b for b in range(4) if (cbp >> (5 - b)) & 1]
    pc = [b for b in (4, 5) if (cbp >> (5 - b)) & 1]
    bits, (f0y, f0cb, f0cr), (f1y, f1cb, f1cr) = E.encode(pl, pc, q, 30)
    ph = E.parse_header(bits)
    if ph is None:
        return None
    start, table, mv_idx, clean, st = ph
    if not clean:
        return None
    seg = bits[start:]
    mvcode = E.MVCODE[mv_idx].get((4, 4))
    if mvcode is None:
        return None
    idx = seg.find(mvcode)
    if not (1 <= idx <= 22):
        return None
    K = seg[:idx]; tail = seg[idx:]
    # locate cbp_index in the header: re-parse to the byte position
    i = 1 + 5  # ptype + qscale
    st2 = bits[i:i+2]; i += 2
    if st2 == "01":
        i += E.NMB
    elif st2 in ("10", "11"):
        nrow = E.MBH if st2 == "10" else E.MBW
        for _ in range(nrow):
            b = bits[i]; i += 1
            if b == "0":
                i += (E.MBW if st2 == "10" else E.MBH)
    pre = bits[:i]            # up to (not incl) cbp_index
    _, j = E.decode012(bits, i)
    mid = bits[j:start]       # mspel+abt+per_mb_rl+rl+dc+mv (unchanged)
    # capture the host AVI (the c.avi just produced) and its P-packet location for splicing
    avi = bytearray(open(f"{TMP}/c.avi", "rb").read())
    pbytes = bytes(int(bits[k:k+8], 2) for k in range(0, len(bits), 8))
    poff = bytes(avi).find(pbytes)
    return pre, mid, K, tail, mv_idx, start, (f1y, f1cb, f1cr), (f0y, f0cb, f0cr), (avi, poff, len(pbytes))


def decode_spliced(newbits, host):
    avi0, poff, plen = host
    b = bytearray(int(newbits[k:k+8], 2) for k in range(0, len(newbits) - len(newbits) % 8, 8))
    if len(b) < plen:
        b += bytes(plen - len(b))
    avi = bytearray(avi0)
    avi[poff:poff+plen] = bytes(b[:plen])
    open(f"{TMP}/v.avi", "wb").write(avi)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", f"{TMP}/v.avi", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True).stdout
    fsz = W * H * 3 // 2
    if len(out) < 2 * fsz:
        return None
    y = np.frombuffer(out[fsz:fsz+W*H], np.uint8).reshape(H, W).astype(np.float64)
    return y


def read_cbp_clean(y, f0y, mx=4, my=4):
    coded = []
    for (r, c) in [(0, 0), (0, 8), (8, 0), (8, 8)]:
        res = np.abs(E.mc(f0y, r, c, mx, my, 8) - y[r:r+8, c:c+8]).sum()
        coded.append(1 if res > 60 else 0)
    return coded  # luma block coded flags (block0..3)


def verify(L, table, cbps, q):
    """Return the cbp in `cbps` whose real tail makes free-leaf L decode cleanly."""
    cbpidx = cbpidx_for_table(q, table)
    best = None
    for C in cbps:
        rt = real_tail(C, q)
        if rt is None:
            continue
        pre, mid, K, tail, mv_idx, start, p1, p0, host = rt
        newbits = pre + cbpidx + mid + L + tail
        y = decode_spliced(newbits, host)
        if y is None:
            continue
        f0y = p0[0]
        # alignment score: does MB0 luma match the cbp-C reconstruction (real frame p1)?
        ref_mb = p1[0][0:16, 0:16]
        err = np.abs(y[0:16, 0:16] - ref_mb).sum()
        if best is None or err < best[0]:
            best = (err, C)
    return best


if __name__ == "__main__":
    FREE = {1: ['0111', '10110', '1010100000']}
    TGT = {1: [28, 30, 60]}
    from collections import Counter
    for table, leaves in FREE.items():
        for L in leaves:
            votes = Counter()
            for q in [4, 6, 8, 16, 24]:
                r = verify(L, table, TGT[table], q)
                if r:
                    votes[r[1]] += 1
            print(f"table {table} leaf {L:>12} -> cbp {votes.most_common(1)[0][0] if votes else '?'} (votes {dict(votes)})")
