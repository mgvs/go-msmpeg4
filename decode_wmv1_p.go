package msmpeg4

import "image"

// decodeInterBlockWMV1 decodes one 8×8 inter block with the WMV1 inter scan and AC escapes.
func (r *bitReader) decodeInterBlockWMV1(q int, t *tcoefTableSet, st *esc3State, mr *[2][64]int) ([64]float64, bool) {
	var qf [64]int
	pos := 0
	for n := 0; ; n++ {
		if n >= 64 {
			return [64]float64{}, false
		}
		c, ok := r.decodeTCOEFWmv1(t, q, st, mr)
		if !ok {
			return [64]float64{}, false
		}
		pos += c.run
		if pos < 64 {
			qf[wmv1ScanInter[pos]] = c.level
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

// DecodePFrameWMV1 decodes one WMV1 P-frame given the previously decoded reference frame.
// bitRate is the value from the preceding I-frame's ext-header (decides per_mb_rl_table).
func DecodePFrameWMV1(data []byte, ref *image.YCbCr, w, h, bitRate int) (*image.YCbCr, error) {
	r := newBitReader(data)
	r.u(2) // picture coding type (01 = inter)
	q := r.u(5)
	if q == 0 || q > 31 {
		return nil, errDecode
	}
	useMBSkip := r.bit() == 1
	perMBRL := 0
	if bitRate > 50*1024 {
		perMBRL = r.bit()
	}
	if perMBRL != 0 {
		return nil, errUnsupportedConfig
	}
	rcIdx := r.c3() // rl_table_index (luma + chroma + inter)
	dcIdx := r.bit()
	mvIdx := r.bit()

	tcSet := chromaTCOEF[rcIdx] // inter + intra chroma
	lumaSet := lumaTCOEF[rcIdx] // intra luma
	if tcSet == nil || lumaSet == nil {
		return nil, errUnsupportedConfig
	}
	interMR := buildMaxRun(tcSet)
	lumaMR := buildMaxRun(lumaSet)
	dcLuma := dcTables[dcIdx][0]
	dcChro := dcTables[dcIdx][1]
	dcScaler := wmv1YDCScale[q]
	chromaScaler := wmv1CDCScale[q]
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
	}

	gL := newDCGrid(2*mbw, 2*mbh, defL)
	gCb := newDCGrid(mbw, mbh, defC)
	gCr := newDCGrid(mbw, mbh, defC)
	acL := newACGrid(2*mbw, 2*mbh)
	acCb := newACGrid(mbw, mbh)
	acCr := newACGrid(mbw, mbh)
	cgw := 2 * mbw
	codedL := make([]int, cgw*2*mbh)

	type mv2 struct{ x, y int }
	mvGrid := make([]mv2, mbw*mbh)
	esc3 := &esc3State{}

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
				dmvx, dmvy, ok := r.decodeMVVLC(mvIdx)
				if !ok {
					return nil, errDecode
				}
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
				mvx := predx + dmvx
				mvy := predy + dmvy
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

				refW, refH := w, h
				refCW, refCH := (w+1)/2, (h+1)/2
				refY, refCb, refCr := ref.Y, ref.Cb, ref.Cr
				refYStride, refCStride := ref.YStride, ref.CStride
				// H.263 chroma MV = (mv>>1) | (mv&1)
				cmvx := (mvx >> 1) | (mvx & 1)
				cmvy := (mvy >> 1) | (mvy & 1)

				for blk := 0; blk < 6; blk++ {
					coded := (cbp>>(5-blk))&1 == 1
					var mcBuf [64]int
					if blk < 4 {
						r0 := my*16 + (blk/2)*8
						c0 := mx*16 + (blk%2)*8
						mcFill(mcBuf[:], refY, refYStride, refW, refH, r0, c0, mvx, mvy, false)
					} else {
						r0 := my * 8
						c0 := mx * 8
						if blk == 4 {
							mcFill(mcBuf[:], refCb, refCStride, refCW, refCH, r0, c0, cmvx, cmvy, false)
						} else {
							mcFill(mcBuf[:], refCr, refCStride, refCW, refCH, r0, c0, cmvx, cmvy, false)
						}
					}
					if coded {
						mrr := interMR
						coeff, ok := r.decodeInterBlockWMV1(q, tcSet, esc3, mrr)
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
				// --- INTRA MB in P-frame (WMV1) ---
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
					pred, fromLeft := grid.predictDCWmv1(gx, gy, def)
					lev := pred + diff
					grid.set(gx, gy, lev)
					_ = codedL
					var qf [64]int
					qf[0] = lev
					if cbp != 0 {
						coded := (cbp>>(5-blk))&1 == 1
						if coded {
							scan := &wmv1ScanZigzag
							if acPred {
								if fromLeft {
									scan = &wmv1ScanAltVert
								} else {
									scan = &wmv1ScanAltHoriz
								}
							}
							ts, mr := lumaSet, lumaMR
							if blk >= 4 {
								ts, mr = tcSet, interMR
							}
							pos := 1
							for n := 0; ; n++ {
								if n >= 64 {
									return nil, errDecode
								}
								c, ok := r.decodeTCOEFWmv1(ts, q, esc3, mr)
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
