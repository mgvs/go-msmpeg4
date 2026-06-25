package msmpeg4

import (
	"image"
)

// DecodePFrame decodes one MS-MPEG4 v3 P-frame given the previously decoded
// reference frame ref (nil → grey reference).
//
// P-frame header layout (v3):
//
//	[2]  picture coding type = 01
//	[5]  quantizer
//	[1]  use_mb_skip_code
//	[c3] rl_table_index  (single index for all tables in P-frames)
//	[1]  dc_table_index
//	[1]  mv_table_index
func DecodePFrame(data []byte, ref *image.YCbCr, w, h int) (*image.YCbCr, error) {
	r := newBitReader(data)
	r.u(2)      // picture coding type (01)
	q := r.u(5) // quantizer
	if q == 0 {
		return nil, errDecode
	}
	useMBSkip := r.bit() == 1
	rcIdx := r.c3()  // rl_table_index (covers luma+chroma+inter in P-frames)
	dcIdx := r.bit() // dc_table_index
	mvIdx := r.bit() // mv_table_index

	// In P-frames, rcIdx selects the RL table for all block types:
	// inter and intra-chroma use chromaTCOEF[rcIdx], intra-luma uses lumaTCOEF[rcIdx].
	tcSet := chromaTCOEF[rcIdx] // inter + intra chroma
	lumaSet := lumaTCOEF[rcIdx] // intra luma
	if tcSet == nil || lumaSet == nil {
		return nil, errUnsupportedConfig
	}
	dcLuma := dcTables[dcIdx][0]
	dcChro := dcTables[dcIdx][1]
	dcScaler := intraDCScaler(q)
	chromaScaler := chromaIntraDCScaler(q)
	defL := (2048 + dcScaler) / (2 * dcScaler)
	defC := (2048 + chromaScaler) / (2 * chromaScaler)

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
	} else {
		for i := range yPlane {
			yPlane[i] = 128
		}
		for i := range cbPlane {
			cbPlane[i] = 128
		}
		for i := range crPlane {
			crPlane[i] = 128
		}
	}

	// DC grids for intra MBs within the P-frame.
	gL := newDCGrid(2*mbw, 2*mbh, defL)
	gCb := newDCGrid(mbw, mbh, defC)
	gCr := newDCGrid(mbw, mbh, defC)
	acL := newACGrid(2*mbw, 2*mbh)
	acCb := newACGrid(mbw, mbh)
	acCr := newACGrid(mbw, mbh)

	// MV storage (half-pixel luma units) for prediction; skip MBs store (0,0).
	type mv2 struct{ x, y int }
	mvGrid := make([]mv2, mbw*mbh)

	for my := 0; my < mbh; my++ {
		for mx := 0; mx < mbw; mx++ {
			if useMBSkip && r.bit() == 1 {
				mvGrid[my*mbw+mx] = mv2{0, 0}
				continue
			}

			intraMB, cbp, ok := r.decodeMBNonIntra()
			if !ok {
				return nil, errDecode
			}

			if !intraMB {
				// --- INTER MB ---
				dmvx, dmvy, ok := r.decodeMVVLC(mvIdx)
				if !ok {
					return nil, errDecode
				}
				// MV prediction follows H.263: for the first row (my==0) only the left
				// block is available, so pred = left MV. For other rows: median(left, top, top-right).
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
				// else my==0: no top-right; or mx==mbw-1: top-right is outside frame,
				// reads as (0,0) from the border-initialized motion-vector predictor.
				var predx, predy int
				if my == 0 {
					predx = plx
					predy = ply
				} else {
					predx = mvMedian3(plx, ptx, prx)
					predy = mvMedian3(ply, pty, pry)
				}
				mvx := predx + dmvx
				mvy := predy + dmvy
				// H.263 MV range wrapping.
				if mvx <= -64 {
					mvx += 64
				} else if mvx >= 64 {
					mvx -= 64
				}
				if mvy <= -64 {
					mvy += 64
				} else if mvy >= 64 {
					mvy -= 64
				}
				mvGrid[my*mbw+mx] = mv2{mvx, mvy}

				// Reference dimensions for boundary clamping.
				refW, refH := w, h
				refCW, refCH := (w+1)/2, (h+1)/2
				var refY, refCb, refCr []byte
				var refYStride, refCStride int
				if ref != nil {
					refY, refCb, refCr = ref.Y, ref.Cb, ref.Cr
					refYStride, refCStride = ref.YStride, ref.CStride
				} else {
					refY = yPlane
					refYStride = cw
					refCb = cbPlane
					refCr = crPlane
					refCStride = cw / 2
					refW, refH = cw, ch
					refCW, refCH = cw/2, ch/2
				}

				// Chroma MV in half-chroma-pixel units.
				// H.263 chroma MV = (mv>>1) | (mv&1)
				cmvx := (mvx >> 1) | (mvx & 1)
				cmvy := (mvy >> 1) | (mvy & 1)

				for blk := 0; blk < 6; blk++ {
					coded := (cbp>>(5-blk))&1 == 1
					var mcBuf [64]int

					if blk < 4 {
						// Luma: block origin in luma pixels.
						r0 := my*16 + (blk/2)*8
						c0 := mx*16 + (blk%2)*8
						mcFill(mcBuf[:], refY, refYStride, refW, refH, r0, c0, mvx, mvy, false)
					} else {
						// Chroma: block origin in chroma pixels.
						r0 := my * 8
						c0 := mx * 8
						if blk == 4 {
							mcFill(mcBuf[:], refCb, refCStride, refCW, refCH, r0, c0, cmvx, cmvy, false)
						} else {
							mcFill(mcBuf[:], refCr, refCStride, refCW, refCH, r0, c0, cmvx, cmvy, false)
						}
					}

					if coded {
						coeff, ok := r.decodeInterBlock(q, tcSet, 1)
						if !ok {
							return nil, errDecode
						}
						residual := simpleResidual(&coeff)
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
				// --- INTRA MB in P-frame ---
				// Only cbp=0 is currently supported (one confirmed intra code).
				mvGrid[my*mbw+mx] = mv2{0, 0}
				acPred := r.bit() == 1

				for blk := 0; blk < 6; blk++ {
					var diff int
					var ok bool
					if blk < 4 {
						diff, ok = dcLuma.decode(r)
					} else {
						diff, ok = dcChro.decode(r)
					}
					if !ok {
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
					def := defL
					if blk >= 4 {
						def = defC
					}
					pred, fromLeft := grid.predictDC(gx, gy, def, acPred, blk == 0 || blk == 1)
					lev := pred + diff
					grid.set(gx, gy, lev)

					var qf [64]int
					qf[0] = lev

					// AC for intra MBs uses cbp from table_mb_non_intra.
					// cbp=0 means no block is AC-coded, so skip AC decode.
					if cbp != 0 {
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
							pos := 1
							ts := tcSet
							if blk < 4 {
								ts = lumaSet
							}
							for n := 0; ; n++ {
								if n >= 64 {
									return nil, errDecode
								}
								c, ok := r.decodeTCOEF(ts, 0)
								if !ok {
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
					blkScaler := dcScaler
					if blk >= 4 {
						blkScaler = chromaScaler
					}
					coeff[0] = float64(qf[0] * blkScaler)
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
	}

	copyPlane(img.Y, yPlane, img.YStride, cw, w, h)
	copyPlane(img.Cb, cbPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	copyPlane(img.Cr, crPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	return img, nil
}

// mvMedian3 returns the median of three motion vector component values.
func mvMedian3(a, b, c int) int {
	if a > b {
		a, b = b, a
	}
	if b > c {
		b = c
	}
	if a > b {
		b = a
	}
	return b
}

// halfPix decomposes a half-pixel coordinate into (integer_offset, frac_bit).
// frac_bit is 0 or 1; integer_offset is in full pixels.
func halfPix(v int) (ip, frac int) {
	frac = ((v % 2) + 2) % 2
	ip = (v - frac) / 2
	return
}

// mcFill fills dst[0..63] with the motion-compensated prediction for one 8×8 block.
// src is the reference plane (stride × height), blockR/C is the block origin in
// the plane, dvx/dvy is the motion vector in half-pixel units of that plane.
// noRound selects truncated half-pel averaging (MS no_rounding) instead of the +1 rounded form.
func mcFill(dst []int, src []byte, stride, planeW, planeH, blockR, blockC, dvx, dvy int, noRound bool) {
	r2, r4 := 1, 2
	if noRound {
		r2, r4 = 0, 1
	}
	ix, fx := halfPix(dvx)
	iy, fy := halfPix(dvy)

	clampR := func(r int) int {
		if r < 0 {
			return 0
		}
		if r >= planeH {
			return planeH - 1
		}
		return r
	}
	clampC := func(c int) int {
		if c < 0 {
			return 0
		}
		if c >= planeW {
			return planeW - 1
		}
		return c
	}

	for i := 0; i < 8; i++ {
		for j := 0; j < 8; j++ {
			sr := clampR(blockR + i + iy)
			sc := clampC(blockC + j + ix)
			a := int(src[sr*stride+sc])
			if fx == 0 && fy == 0 {
				dst[i*8+j] = a
				continue
			}
			b := int(src[sr*stride+clampC(blockC+j+ix+1)])
			c := int(src[clampR(blockR+i+iy+1)*stride+sc])
			d := int(src[clampR(blockR+i+iy+1)*stride+clampC(blockC+j+ix+1)])
			switch {
			case fx == 1 && fy == 0:
				dst[i*8+j] = (a + b + r2) >> 1
			case fx == 0 && fy == 1:
				dst[i*8+j] = (a + c + r2) >> 1
			default: // fx==1 && fy==1
				dst[i*8+j] = (a + b + c + d + r4) >> 2
			}
		}
	}
}

// decodeInterBlock decodes one 8×8 inter block (all 64 coefficients as TCOEF)
// and returns dequantised floating-point coefficients in raster order.
func (r *bitReader) decodeInterBlock(q int, t *tcoefTableSet, runDiff int) ([64]float64, bool) {
	var qf [64]int
	pos := 0
	for n := 0; ; n++ {
		if n >= 64 {
			return [64]float64{}, false
		}
		c, ok := r.decodeTCOEF(t, runDiff)
		if !ok {
			return [64]float64{}, false
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
	var coeff [64]float64
	for i, lv := range qf {
		if lv != 0 {
			coeff[i] = dequantAC(lv, q)
		}
	}
	return coeff, true
}

// writeIntBlock writes an integer MC block (no IDCT, just clamp) to the work planes.
func writeIntBlock(blk, mx, my, cw int, src [64]int, y, cb, cr []byte) {
	var dst []byte
	var stride, r0, c0 int
	switch {
	case blk < 4:
		dst, stride = y, cw
		r0, c0 = (my*2+blk/2)*8, (mx*2+blk%2)*8
	case blk == 4:
		dst, stride = cb, cw/2
		r0, c0 = my*8, mx*8
	default:
		dst, stride = cr, cw/2
		r0, c0 = my*8, mx*8
	}
	for i := 0; i < 8; i++ {
		for j := 0; j < 8; j++ {
			dst[(r0+i)*stride+c0+j] = clampByte(float64(src[i*8+j]))
		}
	}
}
