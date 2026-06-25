"""wmv1_decode.py — minimal WMV1 (WMV7) intra-frame parser built on the v3 VLC tables
(MCBPC / DC / RL are shared with v3, verified). Used to (a) discover the WMV1 picture-header
layout by brute force and (b) read each block's AC (run,level,last) so we can derive the
WMV1 scan tables. Black-box: parses ffmpeg-encoded WMV1 bytes; reuses our own reversed tables.
"""
import os
import re
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_rl(varname):
    for f in ("tcoef_table.go", "tcoef_tables_extra.go", "tcoef_table_inter.go"):
        m = re.search(rf'var {varname} = \[\]tcoefCode\{{(.*?)\n\}}', open(f"{PKG}/{f}").read(), re.DOTALL)
        if not m:
            continue
        d = {}
        for r, l, la, ln, code in re.findall(r'\{\s*(?:run:\s*)?(\d+),\s*(?:level:\s*)?(\d+),\s*(?:last:\s*)?(\d+),\s*(?:length:\s*)?(\d+),\s*(?:code:\s*)?0b([01]+)\s*\}', m.group(1)):
            d[code.zfill(int(ln))] = (int(r), int(l), int(la))
        return d
    raise KeyError(varname)

def _load_maxlev(varname):
    txt = open(f"{PKG}/tcoef_tables_extra.go").read() + open(f"{PKG}/tcoef_table.go").read() + open(f"{PKG}/tcoef_table_inter.go").read()
    m = re.search(rf'var {varname} = \[2\]\[64\]int\{{\s*\{{([^}}]*)\}},\s*\{{([^}}]*)\}}', txt, re.DOTALL)
    return [[int(x) for x in m.group(1).split(",")], [int(x) for x in m.group(2).split(",")]]

# escape codes/lengths per RL table (from buildTcoefSet in tcoef.go — our own code)
LUMA = {0: (_load_rl("tcoefTable0VLC"), "0010110", _load_maxlev("maxlevTable0")),
        1: (_load_rl("tcoefTable2VLC"), "001001010", _load_maxlev("maxlevTable2")),
        2: (_load_rl("tcoefLumaVLC"), "0000011", _load_maxlev("maxlevLuma"))}
CHROMA = {0: (_load_rl("tcoefTable1VLC"), "000001101", _load_maxlev("maxlevTable1")),
          1: (_load_rl("tcoefChromaVLC"), "101101001", _load_maxlev("maxlevChroma")),
          2: (_load_rl("tcoefInterVLC"), "0000011", _load_maxlev("maxlevInter"))}


def _load_dc():
    txt = open(f"{PKG}/dc_table.go").read()
    tabs = {}
    for dc in (0, 1):
        for ch in (0, 1):
            m = re.search(rf'dcRaw{dc}_{ch} := map\[string\]int\{{(.*?)\n\t\}}', txt, re.DOTALL)
            d = {}
            for bits, val in re.findall(r'"([01]+)":\s*(\d+)', m.group(1)):
                d[bits] = int(val)
            tabs[(dc, ch)] = d
    return tabs
DC = _load_dc()
DCMAX = 119


class BR:
    def __init__(s, b): s.b, s.i = b, 0
    def bit(s):
        v = int(s.b[s.i]); s.i += 1; return v
    def u(s, n):
        v = int(s.b[s.i:s.i+n], 2); s.i += n; return v
    def left(s): return len(s.b) - s.i


def dc_decode(r, tab):
    acc = ""
    for _ in range(30):
        acc += s_bit(r)
        if acc in tab:
            mag = tab[acc]
            if mag == DCMAX:
                val = r.u(8)
                return -val if r.bit() else val
            if mag == 0:
                return 0
            return -mag if r.bit() else mag
    raise ValueError("dc")

def s_bit(r):
    return "1" if r.bit() else "0"


def rl_decode(r, tbl, esc, maxlev):
    # returns (run, level, last)
    if r.b[r.i:r.i+len(esc)] == esc:
        r.i += len(esc)
        if r.bit() == 1:        # mode 1: level escape
            run, lv, last = _direct(r, tbl)
            return run, lv + maxlev[last][run], last
        if r.bit() == 1:        # mode 2: run escape (base as-is)
            return _direct(r, tbl)
        last = r.bit(); run = r.u(6); level = r.u(8)   # mode 3: literal
        if level >= 128:
            level -= 256
        return run, level, last
    return _direct(r, tbl)

def _direct(r, tbl):
    acc = ""
    for _ in range(18):
        acc += s_bit(r)
        if acc in tbl:
            run, lv, last = tbl[acc]
            if r.bit() == 1:
                lv = -lv
            return run, lv, last
    raise ValueError("rl")


# MCBPC (table_mb_intra) — load from mcbpc_table.go
def _load_mcbpc():
    txt = open(f"{PKG}/mcbpc_table.go").read()
    d = {}
    for ln, code, cb, cr, y0, y1, y2, y3 in re.findall(r'\{(\d+),\s*0b([01]+),\s*(\d),\s*(\d),\s*(\d),\s*(\d),\s*(\d),\s*(\d)\}', txt):
        d[code.zfill(int(ln))] = (int(cb), int(cr), int(y0), int(y1), int(y2), int(y3))
    return d
MCBPC = _load_mcbpc()

def mcbpc_decode(r):
    acc = ""
    for _ in range(14):
        acc += s_bit(r)
        if acc in MCBPC:
            return MCBPC[acc]
    raise ValueError("mcbpc")


def _maxrun(maxlev):
    # maxrun[last][level] = max run for that (level,last), derived from maxlev[last][run]
    mr = [[0] * 64 for _ in range(2)]
    for last in range(2):
        for run in range(64):
            lv = maxlev[last][run]
            if lv > 0 and run > mr[last][lv if lv < 64 else 63]:
                mr[last][lv if lv < 64 else 63] = run
    return mr


def wmv1_ac_positions(r, tbl, esc, maxlev, qscale, st):
    """Decode block0's AC coefficients with the full WMV1 escape logic; return list of
    (scan_index i, last). st = per-frame ESC3 state {'ll':None,'rl':None}. Intra: i starts 0,
    run_diff=1 (only used in second escape)."""
    mr = _maxrun(maxlev)
    out = []
    i = 0
    for _ in range(70):
        if r.b[r.i:r.i+len(esc)] == esc:
            r.i += len(esc)
            if r.bit() == 1:                 # first escape (level escape)
                run, lv, last = _peek_direct(r, tbl)
                i += run
                r.bit()                      # sign
            elif r.bit() == 1:               # second escape (run escape)
                run, lv, last = _peek_direct(r, tbl)
                i += run + mr[last][lv if lv < 64 else 63] + 1   # run_diff=1
                r.bit()                      # sign
            else:                            # third escape (ESC3)
                last = r.bit()
                if st['ll'] is None:
                    if qscale < 8:
                        ll = r.u(3)
                        if ll == 0:
                            ll = 8 + r.bit()
                    else:
                        ll = 2
                        while ll < 8 and r.bit() == 0:
                            ll += 1
                        if ll < 8:
                            r.bit()
                    st['ll'] = ll
                    st['rl'] = r.u(2) + 3
                run = r.u(st['rl'])
                r.bit()                      # sign
                r.u(st['ll'])                # level
                i += run + 1
                if last:
                    i += 192
        else:
            run, lv, last = _peek_direct(r, tbl)
            i += run
            r.bit()                          # sign
        # last-flag via the +192 trick / direct last bit
        if i > 62:
            i -= 192
            real_last = 1
        else:
            real_last = last
        out.append((i, real_last))
        i += 1
        if real_last:
            break
    return out


def _peek_direct(r, tbl):
    acc = ""
    for _ in range(18):
        acc += s_bit(r)
        if acc in tbl:
            return tbl[acc]
    raise ValueError("rl-direct")


def parse_mb0(bits, S, dc_idx, rl_idx):
    """Parse the first MB assuming MCBPC starts at bit S. Returns (ok, end_i, block_acs)."""
    try:
        r = BR(bits); r.i = S
        cb, cr, y0, y1, y2, y3 = mcbpc_decode(r)
        # CBP prediction at MB(0,0): a=left,b=topleft,c=top all 0 except in-MB neighbours
        a0 = y0
        a1 = y1 ^ a0
        a2 = y2 ^ a0
        pred3 = a2 if a0 == a1 else a1
        a3 = y3 ^ pred3
        coded = [a0, a1, a2, a3, cb, cr]
        ac_pred = r.bit()
        block_acs = []
        for blk in range(6):
            dctab = DC[(dc_idx, 0 if blk < 4 else 1)]
            dc_decode(r, dctab)
            acs = []
            if coded[blk]:
                tbl, esc, mx = LUMA[rl_idx] if blk < 4 else CHROMA[rl_idx]
                while True:
                    run, lv, last = rl_decode(r, tbl, esc, mx)
                    acs.append((run, abs(lv), last))
                    if last == 1:
                        break
                    if len(acs) > 64:
                        return False, r.i, None
            block_acs.append(acs)
        # success if we consumed everything except <8 bits of pad
        if r.left() < 8:
            return True, r.i, block_acs
        return False, r.i, block_acs
    except (ValueError, IndexError):
        return False, -1, None


def discover_and_parse(bits):
    """Brute-force header layout; return (S, dc_idx, rl_idx, ac_pred_bit, block_acs)."""
    for S in range(12, 20):
        for dc_idx in (0, 1):
            for rl_idx in (2, 0, 1):
                ok, end, acs = parse_mb0(bits, S, dc_idx, rl_idx)
                if ok:
                    ac_pred = int(bits[S + _mcbpc_len(bits, S)])
                    return S, dc_idx, rl_idx, ac_pred, acs
    return None

def _mcbpc_len(bits, S):
    acc = ""
    for ch in bits[S:S+14]:
        acc += ch
        if acc in MCBPC:
            return len(acc)
    return 0
