package msmpeg4

import "image"

// decodeWMV2MBType decodes the WMV2 P-frame mb_type VLC for the given cbp_table_index, returning
// (intra, cbp). Tables are 377/384 complete; an unknown codeword yields ok=false.
func (r *bitReader) decodeWMV2MBType(tableIdx int) (intra bool, cbp int, ok bool) {
	t := wmv2MBTypeVLC[tableIdx]
	acc := ""
	for range 22 {
		acc += string(rune('0' + r.bit()))
		if v, found := t[acc]; found {
			return v[0] == 1, v[1], true
		}
	}
	return false, 0, false
}

// wmv2CBPTableIndex = map[(q>10)+(q>20)][cbp_index].
var wmv2CBPMap = [3][3]int{{0, 2, 1}, {1, 0, 2}, {2, 1, 0}}

// DecodePFrameWMV2 decodes one WMV2 (Windows Media Video 8) P-frame given the reference frame, the
// codec extradata (feature flags) and the current no_rounding state (it toggles each P-frame: 0 on
// the first P after an I, 1 on the next, …). ABT (abt_type≠0) and J-frames are not yet supported.
func DecodePFrameWMV2(data []byte, ref *image.YCbCr, w, h int, extradata []byte, noRound bool) (*image.YCbCr, error) {
	mspelBit, abtFlag, perMBRLBit, loopFilter := true, true, true, false
	if len(extradata) >= 4 {
		e := newBitReader(extradata)
		e.u(5)
		e.u(11)
		mspelBit = e.bit() == 1
		loopFilter = e.bit() == 1
		abtFlag = e.bit() == 1
		e.bit() // j_type_bit
		e.bit() // top_left_mv_flag
		perMBRLBit = e.bit() == 1
	}

	r := newBitReader(data)
	if r.bit() != 1 { // picture coding type: 1 = P
		return nil, errUnsupportedConfig
	}
	q := r.u(5)
	if q == 0 || q > 31 {
		return nil, errDecode
	}
	mbw, mbh := (w+15)/16, (h+15)/16
	nmb := mbw * mbh

	// parse_mb_skip: skip_type(2) + skip bits -> per-MB skip flags.
	skip := make([]bool, nmb)
	switch r.u(2) {
	case 0: // NONE: all coded
	case 1: // MPEG: one bit per MB
		for i := 0; i < nmb; i++ {
			skip[i] = r.bit() == 1
		}
	case 2: // ROW: bit per row; if 0, one bit per MB in the row
		for my := 0; my < mbh; my++ {
			if r.bit() == 1 {
				for mx := 0; mx < mbw; mx++ {
					skip[my*mbw+mx] = true
				}
			} else {
				for mx := 0; mx < mbw; mx++ {
					skip[my*mbw+mx] = r.bit() == 1
				}
			}
		}
	default: // COL
		for mx := 0; mx < mbw; mx++ {
			if r.bit() == 1 {
				for my := 0; my < mbh; my++ {
					skip[my*mbw+mx] = true
				}
			} else {
				for my := 0; my < mbh; my++ {
					skip[my*mbw+mx] = r.bit() == 1
				}
			}
		}
	}

	cbpIndex := r.c3()
	cbpTableIdx := wmv2CBPMap[boolToInt(q > 10)+boolToInt(q > 20)][cbpIndex]
	mspel := false
	if mspelBit {
		mspel = r.bit() == 1
	}
	perMBABT := false
	abtType := 0
	if abtFlag {
		perMBABT = r.bit() == 0 // per_mb_abt = bit ^ 1
		if !perMBABT {
			abtType = r.c3()
		}
	}
	perMBRL := 0
	if perMBRLBit {
		perMBRL = r.bit()
	}
	if perMBRL != 0 {
		return nil, errUnsupportedConfig
	}
	rcIdx := r.c3()
	dcIdx := r.bit()
	mvIdx := r.bit()
	if perMBABT || abtType != 0 {
		return nil, errUnsupportedConfig // ABT not yet implemented
	}

	tcSet := chromaTCOEF[rcIdx]
	lumaSet := lumaTCOEF[rcIdx]
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

	cw, ch := mbw*16, mbh*16
	img := image.NewYCbCr(image.Rect(0, 0, w, h), image.YCbCrSubsampleRatio420)
	yPlane := make([]byte, cw*ch)
	cbPlane := make([]byte, (cw/2)*(ch/2))
	crPlane := make([]byte, (cw/2)*(ch/2))
	copyPlane(yPlane, ref.Y, cw, ref.YStride, w, h)
	copyPlane(cbPlane, ref.Cb, cw/2, ref.CStride, (w+1)/2, (h+1)/2)
	copyPlane(crPlane, ref.Cr, cw/2, ref.CStride, (w+1)/2, (h+1)/2)

	gL := newDCGrid(2*mbw, 2*mbh, defL)
	gCb := newDCGrid(mbw, mbh, defC)
	gCr := newDCGrid(mbw, mbh, defC)
	acL := newACGrid(2*mbw, 2*mbh)
	acCb := newACGrid(mbw, mbh)
	acCr := newACGrid(mbw, mbh)

	type mv2 struct{ x, y int }
	mvGrid := make([]mv2, mbw*mbh)
	esc3 := &esc3State{}

	for my := 0; my < mbh; my++ {
		for mx := 0; mx < mbw; mx++ {
			if skip[my*mbw+mx] {
				mvGrid[my*mbw+mx] = mv2{0, 0}
				continue
			}
			intraMB, cbp, ok := r.decodeWMV2MBType(cbpTableIdx)
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
				hshift := 0
				if mspel && (mvx|mvy)&1 != 0 {
					hshift = r.bit()
				}

				for blk := 0; blk < 6; blk++ {
					coded := (cbp>>(5-blk))&1 == 1
					var mcBuf [64]int
					if blk < 4 {
						if mspel {
							mspelLumaBlock(mcBuf[:], ref.Y, ref.YStride, w, h, mx, my, blk, mvx, mvy, hshift, noRound)
						} else {
							r0 := my*16 + (blk/2)*8
							c0 := mx*16 + (blk%2)*8
							mcFill(mcBuf[:], ref.Y, ref.YStride, w, h, r0, c0, mvx, mvy, noRound)
						}
					} else {
						chRef := ref.Cb
						if blk == 5 {
							chRef = ref.Cr
						}
						// msmpeg4 chroma MV = mv>>2 (integer) + (mv&3)!=0 (half-pel) — same for
						// mspel and non-mspel (the H.263 chroma-MV rounding).
						mspelChromaBlock(mcBuf[:], chRef, ref.CStride, (w+1)/2, (h+1)/2, mx, my, mvx, mvy, noRound)
					}
					if coded {
						coeff, ok := r.decodeInterBlockWMV1(q, tcSet, esc3, interMR)
						if !ok {
							return nil, errDecode
						}
						residual := wmv2Residual(&coeff)
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
				// --- INTRA MB in WMV2 P-frame (cbp direct, no prediction) ---
				mvGrid[my*mbw+mx] = mv2{0, 0}
				acPred := r.bit() == 1
				for blk := 0; blk < 6; blk++ {
					var diff int
					var ok2 bool
					if blk < 4 {
						diff, ok2 = dcLuma.decode(r)
					} else {
						diff, ok2 = dcChro.decode(r)
					}
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
					def := defL
					if blk >= 4 {
						def = defC
					}
					pred, fromLeft := grid.predictDCWmv1(gx, gy, def)
					lev := pred + diff
					grid.set(gx, gy, lev)
					var qf [64]int
					qf[0] = lev
					if (cbp>>(5-blk))&1 == 1 {
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
							c, ok3 := r.decodeTCOEFWmv1(ts, q, esc3, mr)
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
					px := wmv2Residual(&coeff)
					writeBlock(blk, mx, my, cw, px, yPlane, cbPlane, crPlane)
				}
			}
		}
	}
	if loopFilter {
		// H.263 in-loop deblocking. NOTE: uniform (no-skip) version — exact when every MB is
		// coded (skip_type=NONE); skip-aware filtering is a TODO for frames with skipped MBs.
		applyH263LoopFilter(yPlane, cbPlane, crPlane, cw, ch, mbw, mbh, q)
	}
	copyPlane(img.Y, yPlane, img.YStride, cw, w, h)
	copyPlane(img.Cb, cbPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	copyPlane(img.Cr, crPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	return img, nil
}

// wmv2Residual runs the integer WMV2 IDCT over the (integer-valued) dequantized coefficients.
func wmv2Residual(coeff *[64]float64) [64]float64 {
	var b [64]int
	for i, c := range coeff {
		b[i] = int(c)
	}
	wmv2IDCT(&b)
	var out [64]float64
	for i, v := range b {
		out[i] = float64(v)
	}
	return out
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
