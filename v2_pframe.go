package msmpeg4

import "image"

// --- v2 P-frame VLC tables ---
//
// H.263 MVD VLC (ITU-T H.263 Table 14) — the open standard motion-vector differential code;
// magnitude 0..32 -> (code,length). Cross-checked against the encoder black-box (re/v2/):
// |MVD| 2->"001", 4->"000011", 6->"0000100".
var h263MVTab = [33][2]int{
	{1, 1}, {1, 2}, {1, 3}, {1, 4}, {3, 6}, {5, 7}, {4, 7}, {3, 7}, {11, 9}, {10, 9}, {9, 9},
	{17, 10}, {16, 10}, {15, 10}, {14, 10}, {13, 10}, {12, 10}, {11, 10}, {10, 10}, {9, 10},
	{8, 10}, {7, 10}, {6, 10}, {5, 10}, {4, 10}, {7, 11}, {6, 11}, {5, 11}, {4, 11}, {3, 11},
	{2, 11}, {3, 12}, {2, 12},
}

var h263MVCode = func() map[string]int {
	m := make(map[string]int, 33)
	for mag, cl := range h263MVTab {
		bits := ""
		for i := cl[1] - 1; i >= 0; i-- {
			bits += string(rune('0' + ((cl[0] >> i) & 1)))
		}
		m[bits] = mag
	}
	return m
}()

// v2 P-frame mb_type VLC (8 codes) — MS-specific. Structure reverse-engineered in re/v2/ and
// every codeword verified bit-exact via full-frame PSNR against the reference decoder (inter
// codes by global-shift P-frames, intra codes by scene-change P-frames). Each codeword maps to
// a value 0..7: bit2 = intra flag, bits1..0 = chroma cbp. Stored as {intra, cbpChroma}.
var v2MBTypeVLC = map[string][2]int{
	"1":       {0, 0}, // inter, cbpc 0
	"00":      {0, 1},
	"011":     {0, 2},
	"01001":   {0, 3},
	"0101":    {1, 0}, // intra, cbpc 0
	"0100001": {1, 1},
	"0100000": {1, 2},
	"010001":  {1, 3},
}

func (r *bitReader) decodeV2MBType() (intra bool, cbpc int, ok bool) {
	acc := ""
	for range 8 {
		acc += string(rune('0' + r.bit()))
		if v, found := v2MBTypeVLC[acc]; found {
			return v[0] == 1, v[1], true
		}
	}
	return false, 0, false
}

// decodeV2MV reads one H.263 MVD component and applies it to the predictor (f_code=1).
func (r *bitReader) decodeV2MV(pred int) (int, bool) {
	acc := ""
	for range 13 {
		acc += string(rune('0' + r.bit()))
		if mag, found := h263MVCode[acc]; found {
			if mag == 0 {
				return pred, true
			}
			val := mag
			if r.bit() == 1 {
				val = -val
			}
			val += pred
			if val <= -64 {
				val += 64
			} else if val >= 64 {
				val -= 64
			}
			return val, true
		}
	}
	return 0, false
}

// decodeV2CBPYInter reads the standard H.263 CBPY VLC (reusing v2CBPYTable) -> 0..15.
func (r *bitReader) decodeV2CBPYInter() (int, bool) {
	acc := ""
	for range 6 {
		acc += string(rune('0' + r.bit()))
		if v, ok := v2CBPYTable[acc]; ok {
			return v, true
		}
	}
	return 0, false
}

// DecodePFrameV2 decodes one MS-MPEG4 v2 (MP42/DIV2) P-frame given the reference frame.
func DecodePFrameV2(data []byte, ref *image.YCbCr, w, h int) (*image.YCbCr, error) {
	r := newBitReader(data)
	r.u(2)      // pictype = 01
	q := r.u(5)
	if q == 0 {
		return nil, errDecode
	}
	useMBSkip := r.bit() == 1

	// v2 fixed selectors: rl=2 (luma + chroma + inter), dc not used (scaler=8), mv=0.
	const dcScale = 8
	const defPred = 128
	lumaSet := lumaTCOEF[2]
	chromaSet := chromaTCOEF[2]   // inter + intra chroma
	if lumaSet == nil || chromaSet == nil {
		return nil, errUnsupportedConfig
	}

	mbw, mbh := (w+15)/16, (h+15)/16
	cw, ch := mbw*16, mbh*16
	img := image.NewYCbCr(image.Rect(0, 0, w, h), image.YCbCrSubsampleRatio420)
	yPlane := make([]byte, cw*ch)
	cbPlane := make([]byte, (cw/2)*(ch/2))
	crPlane := make([]byte, (cw/2)*(ch/2))
	if ref != nil {
		copyPlane(yPlane, ref.Y, cw, ref.YStride, w, h)
		copyPlane(cbPlane, ref.Cb, cw/2, ref.CStride, (w+1)/2, (h+1)/2)
		copyPlane(crPlane, ref.Cr, cw/2, ref.CStride, (w+1)/2, (h+1)/2)
	}

	gL := newDCGrid(2*mbw, 2*mbh, defPred)
	gCb := newDCGrid(mbw, mbh, defPred)
	gCr := newDCGrid(mbw, mbh, defPred)
	acL := newACGrid(2*mbw, 2*mbh)
	acCb := newACGrid(mbw, mbh)
	acCr := newACGrid(mbw, mbh)

	type mv2 struct{ x, y int }
	mvGrid := make([]mv2, mbw*mbh)

	for my := 0; my < mbh; my++ {
		for mx := 0; mx < mbw; mx++ {
			if useMBSkip && r.bit() == 1 {
				mvGrid[my*mbw+mx] = mv2{0, 0}
				continue
			}
			intraMB, cbpc, ok := r.decodeV2MBType()
			if !ok {
				return nil, errDecode
			}

			if !intraMB {
				cbpy, ok := r.decodeV2CBPYInter()
				if !ok {
					return nil, errDecode
				}
				cbp := (cbpy << 2) | cbpc
				if (cbp & 3) != 3 {
					cbp ^= 0x3C
				}
				// MV prediction: median(left, top, top-right) per H.263; first row uses left.
				var plx, ply, ptx, pty, prx, pry int
				if mx > 0 {
					plx = mvGrid[my*mbw+mx-1].x
					ply = mvGrid[my*mbw+mx-1].y
				}
				if my > 0 {
					ptx = mvGrid[(my-1)*mbw+mx].x
					pty = mvGrid[(my-1)*mbw+mx].y
				}
				if my > 0 && mx < mbw-1 {
					prx = mvGrid[(my-1)*mbw+mx+1].x
					pry = mvGrid[(my-1)*mbw+mx+1].y
				}
				var predx, predy int
				if my == 0 {
					predx, predy = plx, ply
				} else {
					predx = mvMedian3(plx, ptx, prx)
					predy = mvMedian3(ply, pty, pry)
				}
				mvx, ok1 := r.decodeV2MV(predx)
				mvy, ok2 := r.decodeV2MV(predy)
				if !ok1 || !ok2 {
					return nil, errDecode
				}
				mvGrid[my*mbw+mx] = mv2{mvx, mvy}

				refW, refH := w, h
				refCW, refCH := (w+1)/2, (h+1)/2
				// H.263 chroma MV = (mv>>1) | (mv&1)  (rounds half-pel luma MVs).
				cmvx := (mvx >> 1) | (mvx & 1)
				cmvy := (mvy >> 1) | (mvy & 1)
				for blk := 0; blk < 6; blk++ {
					coded := (cbp>>(5-blk))&1 == 1
					var mcBuf [64]int
					if blk < 4 {
						r0 := my*16 + (blk/2)*8
						c0 := mx*16 + (blk%2)*8
						mcFill(mcBuf[:], ref.Y, ref.YStride, refW, refH, r0, c0, mvx, mvy)
					} else {
						r0 := my * 8
						c0 := mx * 8
						if blk == 4 {
							mcFill(mcBuf[:], ref.Cb, ref.CStride, refCW, refCH, r0, c0, cmvx, cmvy)
						} else {
							mcFill(mcBuf[:], ref.Cr, ref.CStride, refCW, refCH, r0, c0, cmvx, cmvy)
						}
					}
					if coded {
						coeff, ok := r.decodeInterBlock(q, chromaSet)
						if !ok {
							return nil, errDecode
						}
						residual := idct8(&coeff)
						var px [64]float64
						for i, v := range mcBuf {
							px[i] = residual[i] + float64(v)
						}
						writeBlock(blk, mx, my, cw, px, yPlane, cbPlane, crPlane)
					} else {
						writeIntBlock(blk, mx, my, cw, mcBuf, yPlane, cbPlane, crPlane)
					}
				}
			} else {
				// --- INTRA MB in v2 P-frame ---
				mvGrid[my*mbw+mx] = mv2{0, 0}
				acPred := r.bit() == 1
				cbpy, ok := r.decodeV2CBPYInter()
				if !ok {
					return nil, errDecode
				}
				cbp := (cbpy << 2) | cbpc
				for blk := 0; blk < 6; blk++ {
					isChroma := blk >= 4
					diff, ok2 := decodeV2DC(r, isChroma)
					if !ok2 {
						return nil, errDecode
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
					coded := (cbp>>(5-blk))&1 == 1
					if coded {
						scan := &scanZigzag
						if acPred {
							if fromLeft {
								scan = &scanAltVert
							} else {
								scan = &scanAltHoriz
							}
						}
						ts := lumaSet
						if isChroma {
							ts = chromaSet
						}
						pos := 1
						for n := 0; ; n++ {
							if n >= 64 {
								return nil, errDecode
							}
							c, ok3 := r.decodeTCOEFV2(ts, 9)
							if !ok3 {
								return nil, errDecode
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
					px := idct8(&coeff)
					writeBlock(blk, mx, my, cw, px, yPlane, cbPlane, crPlane)
				}
			}
		}
	}
	copyPlane(img.Y, yPlane, img.YStride, cw, w, h)
	copyPlane(img.Cb, cbPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	copyPlane(img.Cr, crPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	return img, nil
}
