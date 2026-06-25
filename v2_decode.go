package msmpeg4

import (
	"errors"
	"image"
)

// decodeV2DCLen reads a dc2_vlc VLC code and returns the bit-length prefix (0..8+).
// isChroma selects between the luma table (dc2_vlc[0]) and chroma table (dc2_vlc[1]).
//
// Luma table (dc2_vlc[0]):
//
//	"100"→0  "00"→1  "01"→2  "101"→3  "110"→4  "1110"→5  "11110"→6  "111110"→7  "1111110"→8
//
// Chroma table (dc2_vlc[1]):
//
//	"00"→0  "01"→1  "10"→2  "110"→3  "1110"→4  "11110"→5  "111110"→6  "1111110"→7  "11111110"→8
func decodeV2DCLen(r *bitReader, isChroma bool) int {
	if !isChroma {
		b0 := r.bit()
		if b0 == 0 {
			return 1 + r.bit() // "00"→1, "01"→2
		}
		b1 := r.bit()
		if b1 == 0 {
			if r.bit() == 0 {
				return 0 // "100"→0
			}
			return 3 // "101"→3
		}
		// "11..."
		if r.bit() == 0 {
			return 4 // "110"→4
		}
		if r.bit() == 0 {
			return 5 // "1110"→5
		}
		if r.bit() == 0 {
			return 6 // "11110"→6
		}
		if r.bit() == 0 {
			return 7 // "111110"→7
		}
		if r.bit() == 0 {
			return 8 // "1111110"→8
		}
		return 9
	}

	// chroma
	b0 := r.bit()
	if b0 == 0 {
		return r.bit() // "00"→0, "01"→1
	}
	if r.bit() == 0 {
		return 2 // "10"→2
	}
	if r.bit() == 0 {
		return 3 // "110"→3
	}
	if r.bit() == 0 {
		return 4 // "1110"→4
	}
	if r.bit() == 0 {
		return 5 // "11110"→5
	}
	if r.bit() == 0 {
		return 6 // "111110"→6
	}
	if r.bit() == 0 {
		return 7 // "1111110"→7
	}
	if r.bit() == 0 {
		return 8 // "11111110"→8
	}
	return 9
}

// decodeV2DC reads one v1/v2 DC differential using the dc2_vlc sign-magnitude encoding.
// Returns (diff, ok).
func decodeV2DC(r *bitReader, isChroma bool) (int, bool) {
	l := decodeV2DCLen(r, isChroma)
	if l == 0 {
		return 0, true
	}
	if l > 12 {
		return 0, false
	}
	u := r.u(l)
	var diff int
	if (u>>(l-1))&1 == 1 {
		diff = u // MSB set → positive
	} else {
		diff = -(u ^ ((1 << l) - 1)) // MSB clear → negative
	}
	if l > 8 {
		r.bit() // skip marker bit
	}
	return diff, true
}

// decodeV2CBPC reads the v2_intra_cbpc VLC (the 4-entry intra MCBPC for v2/v1 MBs).
// Returns (cbpc 0..3, ok). The codeword set is a complete prefix tree, so it always
// resolves within 3 bits:
//
//	"1"→0  "000"→1  "001"→2  "01"→3
func (r *bitReader) decodeV2CBPC() (cbpc int, ok bool) {
	if r.bit() == 1 {
		return 0, true // "1"
	}
	if r.bit() == 1 {
		return 3, true // "01"
	}
	if r.bit() == 1 {
		return 2, true // "001"
	}
	return 1, true // "000"
}

// Standard H.263/MPEG-4 CBPY VLC for intra MBs.
// Code → CBPY value; cbpy bit 3..0 = luma blocks 0..3 (1=coded).
var v2CBPYTable = map[string]int{
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

// decodeV2CBPY reads standard H.263 CBPY VLC, returns (cbpy 0–15, ok).
// Stuffing codes with prefix "00000" (any pattern in the unused subtree) are consumed and retried.
func (r *bitReader) decodeV2CBPY() (int, bool) {
	for range 8 {
		acc := ""
		for range 12 {
			b := r.bit()
			if b == 1 {
				acc += "1"
			} else {
				acc += "0"
			}
			if v, ok := v2CBPYTable[acc]; ok {
				return v, true
			}
			// Prefix "00000" is entirely unused in the CBPY table — stuffing domain.
			// Consume bits until the first "1" is seen, then retry.
			if len(acc) >= 5 && acc[:5] == "00000" && b == 1 {
				break // stuffing consumed, retry outer loop
			}
		}
		if len(acc) < 5 || acc[:5] != "00000" {
			return 0, false
		}
	}
	return 0, false
}

var errV2Decode = errors.New("msmpeg4: v2 intra decode failed")

// decodeTCOEFV2 wraps decodeTCOEF with stuffing support.
// After a decode failure, if the reader was sitting on stuffingThreshold+ consecutive
// zero bits followed by a "1", those bits are consumed and the decode is retried.
func (r *bitReader) decodeTCOEFV2(t *tcoefTableSet, stuffingThreshold int) (acCoeff, bool) {
	for range 8 {
		saved := r.pos1()
		c, ok := r.decodeTCOEF(t, 0)
		if ok {
			return c, true
		}
		r.seek(saved)
		zeros := 0
		for zeros < stuffingThreshold+20 {
			if r.bit() != 0 {
				break
			}
			zeros++
		}
		if zeros >= stuffingThreshold {
			continue // stuffing consumed (zeros zeros + 1 bit), retry
		}
		r.seek(saved)
		return acCoeff{}, false
	}
	return acCoeff{}, false
}

// DecodeIntraFrameV2 decodes one MS-MPEG4 v2 (MP42/DIV2) intra frame.
// The header is 12 bits: pictype u(2) + quant u(5) + slice_code u(5).
func DecodeIntraFrameV2(data []byte, w, h int) (*image.YCbCr, error) {
	r := newBitReader(data)
	r.u(2) // pictype (00=intra)
	q := r.u(5)
	if q == 0 {
		return nil, errV2Decode
	}
	r.u(5) // slice_code

	// v2 DC scaler is constant 8 for both luma and chroma.
	const dcScale = 8
	const defPred = 128 // round(1024/dcScale)

	// v2 uses fixed RL-VLC tables: intra luma = the MPEG-4 intra mid-rate table
	// (lumaTCOEF[2]); intra chroma = the inter mid-rate table (chromaTCOEF[2]).
	v2LumaSet := lumaTCOEF[2]
	v2ChromaSet := chromaTCOEF[2]

	mbw, mbh := (w+15)/16, (h+15)/16
	cw, ch := mbw*16, mbh*16
	img := image.NewYCbCr(image.Rect(0, 0, w, h), image.YCbCrSubsampleRatio420)
	yPlane := make([]byte, cw*ch)
	cbPlane := make([]byte, (cw/2)*(ch/2))
	crPlane := make([]byte, (cw/2)*(ch/2))

	gL := newDCGrid(2*mbw, 2*mbh, defPred)
	gCb := newDCGrid(mbw, mbh, defPred)
	gCr := newDCGrid(mbw, mbh, defPred)
	acL := newACGrid(2*mbw, 2*mbh)
	acCb := newACGrid(mbw, mbh)
	acCr := newACGrid(mbw, mbh)

	for my := 0; my < mbh; my++ {
		for mx := 0; mx < mbw; mx++ {
			cbpc, ok := r.decodeV2CBPC()
			if !ok {
				return nil, errV2Decode
			}
			acPred := r.bit() == 1
			cbpy, ok := r.decodeV2CBPY()
			if !ok {
				return nil, errV2Decode
			}
			// CBP: bits 5-2 = luma (from cbpy), bits 1-0 = chroma (from cbpc).
			// cbpy bit 3 → blk0, bit 2 → blk1, bit 1 → blk2, bit 0 → blk3.

			for blk := 0; blk < 6; blk++ {
				isChroma := blk >= 4
				diff, ok2 := decodeV2DC(r, isChroma)
				if !ok2 {
					return nil, errV2Decode
				}
				var gx, gy int
				var grid *dcGrid
				var acg *acGrid
				switch {
				case blk < 4:
					grid, acg, gx, gy = gL, acL, 2*mx+blk%2, 2*my+blk/2
				case blk == 4:
					grid, acg, gx, gy = gCb, acCb, mx, my
				default:
					grid, acg, gx, gy = gCr, acCr, mx, my
				}
				pred, fromLeft := grid.predictDC(gx, gy, defPred, acPred, blk == 0 || blk == 1)
				lev := pred + diff
				grid.set(gx, gy, lev)

				var qf [64]int
				qf[0] = lev

				// Determine coded-bit for this block.
				var coded int
				switch {
				case blk < 4:
					coded = (cbpy >> (3 - blk)) & 1
				case blk == 4:
					coded = (cbpc >> 1) & 1
				default:
					coded = cbpc & 1
				}

				if coded != 0 {
					scan := &scanZigzag
					if acPred {
						if fromLeft {
							scan = &scanAltVert
						} else {
							scan = &scanAltHoriz
						}
					}
					pos := 1
					for n := 0; ; n++ {
						if n >= 64 {
							return nil, errV2Decode
						}
						tcSet := v2LumaSet
						stuffing := 9
						if isChroma {
							tcSet = v2ChromaSet
						}
						c, ok3 := r.decodeTCOEFV2(tcSet, stuffing)
						if !ok3 {
							return nil, errV2Decode
						}
						pos += c.run
						if pos < 64 {
							qf[scan[pos]] = c.level
						}
						pos++
						if c.last {
							break
						}
					}
				}

				if acPred {
					if fromLeft && gx > 0 {
						pr := acg.col[gy*acg.w+gx-1]
						for i := 1; i < 8; i++ {
							qf[i*8] += pr[i]
						}
					} else if !fromLeft && gy > 0 {
						pc := acg.row[(gy-1)*acg.w+gx]
						for j := 1; j < 8; j++ {
							qf[j] += pc[j]
						}
					}
				}

				var rrow, rcol [8]int
				for i := 1; i < 8; i++ {
					rrow[i] = qf[i]
					rcol[i] = qf[i*8]
				}
				acg.row[gy*acg.w+gx] = rrow
				acg.col[gy*acg.w+gx] = rcol

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
