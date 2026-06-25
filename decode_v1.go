package msmpeg4

import "image"

// MS-MPEG4 v1 (MPG4 / MP41 / DIV1) intra-frame decoder. v1 predates v2/v3: the bitstream starts
// with a 32-bit start code, the macroblock layer uses the open H.263 intra MCBPC + CBPY VLCs (no
// bundled v3 table, no AC prediction), luma/chroma DC scale is a constant 8, and intra DC is
// predicted MPEG-1 style (from the previous block of the same component, reset per slice row).
//
// Reverse-engineered black-box (re/v1/NOTES.md): encoder = MS mpg4c32.dll via Wine, pixel oracle
// = ffmpeg msmpeg4v1. Header layout confirmed empirically; MCBPC/CBPY are the open H.263 tables.

// h263IntraMCBPC: code -> {intraQ, cbpc}. For I-frames every MB is intra; type 4 (INTRA+Q) carries
// a 2-bit dquant. Open H.263 Table 8.
var h263IntraMCBPC = map[string][2]int{
	"1":      {0, 0}, // INTRA  cbpc 00
	"001":    {0, 1}, // INTRA  cbpc 01
	"010":    {0, 2}, // INTRA  cbpc 10
	"011":    {0, 3}, // INTRA  cbpc 11
	"0001":   {1, 0}, // INTRA+Q cbpc 00
	"000001": {1, 1},
	"000010": {1, 2},
	"000011": {1, 3},
}

func (r *bitReader) decodeH263IntraMCBPC() (intraQ, cbpc int, ok bool) {
	acc := ""
	for range 12 {
		acc += string(rune('0' + r.bit()))
		if v, found := h263IntraMCBPC[acc]; found {
			return v[0], v[1], true
		}
	}
	return 0, 0, false
}

// h263CBPY: code -> cbpy value (0..15), intra interpretation (no complement). Open H.263 Table 13.
var h263CBPY = map[string]int{
	"0011":   0,
	"00101":  1,
	"00100":  2,
	"1001":   3,
	"00011":  4,
	"0111":   5,
	"000010": 6,
	"1011":   7,
	"00010":  8,
	"000011": 9,
	"0101":   10,
	"1010":   11,
	"0100":   12,
	"1000":   13,
	"0110":   14,
	"11":     15,
}

func (r *bitReader) decodeH263CBPY() (int, bool) {
	acc := ""
	for range 6 {
		acc += string(rune('0' + r.bit()))
		if v, found := h263CBPY[acc]; found {
			return v, true
		}
	}
	return 0, false
}

// DecodeIntraFrameV1 decodes one MS-MPEG4 v1 I-frame to an image.
func DecodeIntraFrameV1(data []byte, w, h int) (*image.YCbCr, error) {
	r := newBitReader(data)
	r.u(32) // start code 0x00000100
	r.u(5)  // frame number (mod 31)
	if r.u(2) != 0 {
		return nil, errDecode // not an I-frame
	}
	q := r.u(5)
	if q == 0 || q > 31 {
		return nil, errDecode
	}
	r.u(5) // slice code (slice height; single slice assumed here)

	const dcScale = 8
	lumaSet := lumaTCOEF[2]
	chromaSet := chromaTCOEF[2]

	mbw, mbh := (w+15)/16, (h+15)/16
	cw, ch := mbw*16, mbh*16
	img := image.NewYCbCr(image.Rect(0, 0, w, h), image.YCbCrSubsampleRatio420)
	yPlane := make([]byte, cw*ch)
	cbPlane := make([]byte, (cw/2)*(ch/2))
	crPlane := make([]byte, (cw/2)*(ch/2))

	// MPEG-1 style DC predictors (one per component), reset at the start of each slice row.
	const dcPredInit = 1024 / dcScale // 128
	for my := 0; my < mbh; my++ {
		predY, predCb, predCr := dcPredInit, dcPredInit, dcPredInit
		for mx := 0; mx < mbw; mx++ {
			intraQ, cbpc, ok := r.decodeH263IntraMCBPC()
			if !ok {
				return nil, errDecode
			}
			cbpy, ok := r.decodeH263CBPY()
			if !ok {
				return nil, errDecode
			}
			if intraQ == 1 {
				r.u(2) // dquant (quantizer change) — not applied yet
			}
			cbp := cbpy<<2 | cbpc

			for blk := 0; blk < 6; blk++ {
				isChroma := blk >= 4
				diff, ok2 := decodeV2DC(r, isChroma)
				if !ok2 {
					return nil, errDecode
				}
				var pred *int
				switch {
				case blk < 4:
					pred = &predY
				case blk == 4:
					pred = &predCb
				default:
					pred = &predCr
				}
				lev := *pred + diff
				*pred = lev

				var qf [64]int
				qf[0] = lev
				coded := (cbp>>(5-blk))&1 == 1
				if coded {
					ts := lumaSet
					if isChroma {
						ts = chromaSet
					}
					pos := 1
					for n := 0; ; n++ {
						if n >= 64 {
							return nil, errDecode
						}
						c, ok3 := r.decodeTCOEFv1(ts)
						if !ok3 {
							return nil, errDecode
						}
						pos += c.run
						if pos < 64 {
							qf[scanZigzag[pos]] = c.level
						}
						pos++
						if c.last {
							break
						}
					}
				}

				var coeff [64]float64
				coeff[0] = float64(qf[0] * dcScale)
				for i := 1; i < 64; i++ {
					if qf[i] != 0 {
						coeff[i] = dequantAC(qf[i], q)
					}
				}
				px := simpleResidual(&coeff)
				writeBlock(blk, mx, my, cw, px, yPlane, cbPlane, crPlane)
			}
		}
	}
	copyPlane(img.Y, yPlane, img.YStride, cw, w, h)
	copyPlane(img.Cb, cbPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	copyPlane(img.Cr, crPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	return img, nil
}

// decodeTCOEFv1 decodes one AC TCOEF for v1, which uses the plain ISO-MPEG4/H.263 escape
// (esc + last(1) + run(6) + level(8, two's-complement signed)) — no variable escape modes.
func (r *bitReader) decodeTCOEFv1(t *tcoefTableSet) (acCoeff, bool) {
	if r.peek(t.escLen) == t.esc {
		r.u(t.escLen)
		last := r.bit()
		run := r.u(6)
		level := r.u(8)
		if level >= 128 {
			level -= 256
		}
		return acCoeff{run: run, level: level, last: last == 1}, true
	}
	code, n := 0, 0
	for n < tcoefMaxLen {
		code = code<<1 | r.bit()
		n++
		if e, ok := t.m[tcoefKey{n, code}]; ok {
			level := e.level
			if r.bit() == 1 {
				level = -level
			}
			return acCoeff{run: e.run, level: level, last: e.last == 1}, true
		}
	}
	return acCoeff{}, false
}
