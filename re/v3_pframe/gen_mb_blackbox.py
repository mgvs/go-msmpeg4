"""gen_mb_blackbox.py — emit pframe_vlc.go from the BLACK-BOX-derived mb_type tables
(/tmp/pf_mbx/mb_inter.json + /tmp/pf_mbi/mb_intra.json). No ffmpeg source is read.

The decodeMBNonIntra and decodeMVVLC helper functions are emitted verbatim (logic only,
no table data). Run with `write` to overwrite; default writes a diff copy.
"""
import os
import json, sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pframe_vlc.go")

HEADER = '''package msmpeg4

// VLC tables for P-frame (inter picture) decoding.
//
// Re-derived black-box: every codeword and its (interMB, cbp) meaning was recovered by
// driving the ffmpeg ENCODER and DECODER as oracles over hand-built clips (see
// re/pframe_mb_extract.py for the inter half and re/pframe_mb_intra.py for the intra
// half) and reading the result back from the emitted bitstream and the decoded pixels.
// No FFmpeg source or Microsoft binary was consulted.

// mbNonIntraVLC: table_mb_non_intra — encodes (interMB, cbp) for each macroblock.
// Stored as [2]int{0=inter / 1=intra, cbp}.
//
// CBP layout: cbp = (CBPY<<2)|CBPC where CBPY has luma blocks (bit3=blk0 .. bit0=blk3)
// and CBPC has chroma (bit1=Cb, bit0=Cr). So cbp>>5&1=blk0, cbp>>4&1=blk1, ..., cbp&1=Cr.
'''

FUNCS = '''
// decodeMBNonIntra decodes one table_mb_non_intra VLC and returns (intraMB, cbp, ok).
func (r *bitReader) decodeMBNonIntra() (intraMB bool, cbp int, ok bool) {
	acc := ""
	for range 22 {
		if r.bit() == 1 {
			acc += "1"
		} else {
			acc += "0"
		}
		if v, found := mbNonIntraVLC[acc]; found {
			return v[0] == 1, v[1], true
		}
	}
	return false, 0, false
}

// decodeMVVLC decodes one MV VLC code and returns (dmvx, dmvy, ok)
// in half-pixel luma units. idx selects the MV table (0 or 1).
//
// The sentinel {-32,-32} in both tables signals an escape: two additional
// 6-bit raw values follow, and dmv = raw - 32.
func (r *bitReader) decodeMVVLC(idx int) (dmvx, dmvy int, ok bool) {
	tbl := mvVLC1
	if idx == 0 {
		tbl = mvVLC0
	}
	acc := ""
	for range 17 {
		if r.bit() == 1 {
			acc += "1"
		} else {
			acc += "0"
		}
		if v, found := tbl[acc]; found {
			if v[0] == -32 && v[1] == -32 {
				// Escape: read 6-bit raw x then 6-bit raw y; dmv = raw - 32.
				rawX := r.u(6)
				rawY := r.u(6)
				return rawX - 32, rawY - 32, true
			}
			return v[0], v[1], true
		}
	}
	return 0, 0, false
}
'''


def emit():
    inter = json.load(open("/tmp/pf_mbx/mb_inter.json"))
    intra = json.load(open("/tmp/pf_mbi/mb_intra.json"))
    entries = []
    for code, v in {**inter, **intra}.items():
        entries.append((code, v[0], v[1]))
    assert len(entries) == 128, len(entries)
    entries.sort(key=lambda e: (len(e[0]), e[0]))
    lines = [HEADER.rstrip("\n"), "var mbNonIntraVLC = func() map[string][2]int {",
             "\t// [2]int{0=inter/1=intra, cbp}", "\traw := map[string][2]int{"]
    for code, a, b in entries:
        lines.append(f'\t\t"{code}": {{{a}, {b}}},')
    lines += ["\t}", "\treturn raw", "}()", FUNCS.rstrip("\n"), ""]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = emit()
    if len(sys.argv) > 1 and sys.argv[1] == "write":
        open(OUT, "w").write(text)
        print(f"wrote {OUT}")
    else:
        open("/tmp/pframe_vlc.new.go", "w").write(text)
        print("wrote /tmp/pframe_vlc.new.go")
