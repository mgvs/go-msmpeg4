package msmpeg4

import "image"

// predictDCWmv1 is predictDC with WMV1's tie-break: dir=top only when abs(a-b) < abs(b-c)
// (strict), so fromLeft (left, alt-vertical) wins on ties — unlike v3's <=.
func (g *dcGrid) predictDCWmv1(x, y, def int) (pred int, fromLeft bool) {
	a, b, c := def, def, def
	if x > 0 {
		a = g.v[y*g.w+x-1]
	}
	if x > 0 && y > 0 {
		b = g.v[(y-1)*g.w+x-1]
	}
	if y > 0 {
		c = g.v[(y-1)*g.w+x]
	}
	fromLeft = abs(a-b) >= abs(b-c)
	if fromLeft {
		pred = a
	} else {
		pred = c
	}
	return
}

// esc3State holds the WMV1 third-escape (ESC3) level/run bit-lengths, read once per frame.
type esc3State struct {
	levelLen, runLen int
	set              bool
}

// buildMaxRun derives maxrun[last][level] = max run for that (level,last) from a table set.
func buildMaxRun(t *tcoefTableSet) *[2][64]int {
	var mr [2][64]int
	for _, e := range t.m {
		if e.level >= 0 && e.level < 64 && e.run > mr[e.last][e.level] {
			mr[e.last][e.level] = e.run
		}
	}
	return &mr
}

// matchDirectWmv1 reads a direct RL codeword from table set t.
func (r *bitReader) matchDirectWmv1(t *tcoefTableSet) (tcoefCode, bool) {
	code, n := 0, 0
	for n < tcoefMaxLen {
		code = code<<1 | r.bit()
		n++
		if e, ok := t.m[tcoefKey{n, code}]; ok {
			return e, true
		}
	}
	return tcoefCode{}, false
}

// decodeTCOEFWmv1 reads one AC coefficient (run, signed level, last) using the WMV1 escape
// rules: same direct RL tables as v3, run_diff=1 (second escape), and the variable-length
// third escape (ESC3). st carries the per-frame ESC3 lengths; mr is maxrun for table t.
func (r *bitReader) decodeTCOEFWmv1(t *tcoefTableSet, q int, st *esc3State, mr *[2][64]int) (acCoeff, bool) {
	if r.peek(t.escLen) == t.esc {
		r.u(t.escLen)
		if r.bit() == 1 { // first escape: level escape
			e, ok := r.matchDirectWmv1(t)
			if !ok {
				return acCoeff{}, false
			}
			level := e.level + (*t.maxlev)[e.last][e.run]
			if r.bit() == 1 {
				level = -level
			}
			return acCoeff{run: e.run, level: level, last: e.last == 1}, true
		}
		if r.bit() == 1 { // second escape: run escape (run_diff = 1 for WMV1)
			e, ok := r.matchDirectWmv1(t)
			if !ok {
				return acCoeff{}, false
			}
			run := e.run + mr[e.last][e.level] + 1
			level := e.level
			if r.bit() == 1 {
				level = -level
			}
			return acCoeff{run: run, level: level, last: e.last == 1}, true
		}
		// third escape (ESC3) — variable length, lengths read once per frame.
		last := r.bit()
		if !st.set {
			var ll int
			if q < 8 {
				ll = r.u(3)
				if ll == 0 {
					ll = 8 + r.bit()
				}
			} else {
				ll = 2
				for ll < 8 && r.peek(1) == 0 {
					r.bit()
					ll++
				}
				if ll < 8 {
					r.bit() // consume the terminating 1
				}
			}
			st.levelLen = ll
			st.runLen = r.u(2) + 3
			st.set = true
		}
		run := r.u(st.runLen)
		sign := r.bit()
		level := r.u(st.levelLen)
		if sign == 1 {
			level = -level
		}
		return acCoeff{run: run, level: level, last: last == 1}, true
	}
	// direct RL-VLC + sign bit
	e, ok := r.matchDirectWmv1(t)
	if !ok {
		return acCoeff{}, false
	}
	level := e.level
	if r.bit() == 1 {
		level = -level
	}
	return acCoeff{run: e.run, level: level, last: e.last == 1}, true
}

// DecodeIntraFrameWMV1 decodes one WMV1 (Windows Media Video 7) intra frame. WMV1 reuses the
// v3 VLC tables (MCBPC / DC / RL) but has a different picture header (WMV ext-header), its own
// scan and DC-scale tables, and the WMV1 AC escape coding. per_mb_rl_table=1 is not handled.
func DecodeIntraFrameWMV1(data []byte, w, h int) (*image.YCbCr, error) {
	r := newBitReader(data)
	r.u(2) // picture coding type (00 = intra)
	q := r.u(5)
	if q == 0 || q > 31 {
		return nil, errDecode
	}
	r.u(5) // slice_code (number-of-slices encoding; single slice for thumbnails)
	// WMV ext-header (17 bits for v3+): fps(5) + bit_rate(11) + flipflop_rounding(1).
	r.u(5)
	bitRate := r.u(11) * 1024
	r.bit() // flipflop_rounding (P-frame only)
	perMBRL := 0
	if bitRate > 50*1024 {
		perMBRL = r.bit()
	}
	rcIdx, rtIdx := 0, 0
	if perMBRL == 0 {
		rcIdx = r.c3()
		rtIdx = r.c3()
	}
	dcIdx := r.bit()

	return wmv1IntraBody(r, q, rtIdx, rcIdx, dcIdx, w, h, perMBRL != 0, false)
}

// wmv1IntraBody decodes the intra-MB layer shared by WMV1 and WMV2 (j_type=0) I-frames: it sets
// up the RL/DC/scan/scaler tables from the header indices and decodes every macroblock. The bit
// reader must be positioned at the start of the first macroblock.
func wmv1IntraBody(r *bitReader, q, rtIdx, rcIdx, dcIdx, w, h int, perMBRL, loopFilter bool) (*image.YCbCr, error) {
	// When per_mb_rl_table is set the RL table index is read per macroblock (any coded block);
	// otherwise it comes from the header (rtIdx/rcIdx). Pre-build max-run tables for both.
	var lumaSets, chromaSets [3]*tcoefTableSet
	var lumaMRs, chromaMRs [3]*[2][64]int
	for i := 0; i < 3; i++ {
		lumaSets[i], chromaSets[i] = lumaTCOEF[i], chromaTCOEF[i]
		if lumaSets[i] != nil {
			lumaMRs[i] = buildMaxRun(lumaSets[i])
		}
		if chromaSets[i] != nil {
			chromaMRs[i] = buildMaxRun(chromaSets[i])
		}
	}
	lumaSet := lumaSets[rtIdx]
	chromaSet := chromaSets[rcIdx]
	lumaMR, chromaMR := lumaMRs[rtIdx], chromaMRs[rcIdx]
	if !perMBRL && (lumaSet == nil || chromaSet == nil) {
		return nil, errUnsupportedConfig
	}
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

	gL := newDCGrid(2*mbw, 2*mbh, defL)
	gCb := newDCGrid(mbw, mbh, defC)
	gCr := newDCGrid(mbw, mbh, defC)
	acL := newACGrid(2*mbw, 2*mbh)
	acCb := newACGrid(mbw, mbh)
	acCr := newACGrid(mbw, mbh)
	cgw := 2 * mbw
	codedL := make([]int, cgw*2*mbh)

	esc3 := &esc3State{} // ESC3 lengths persist for the whole frame

	for my := 0; my < mbh; my++ {
		for mx := 0; mx < mbw; mx++ {
			cbp6, ok := r.decodeMCBPC()
			if !ok {
				return nil, errDecode
			}
			for i := 0; i < 4; i++ {
				bx, by := 2*mx+i%2, 2*my+i/2
				var left, topLeft, top int
				if bx > 0 {
					left = codedL[by*cgw+bx-1]
				}
				if bx > 0 && by > 0 {
					topLeft = codedL[(by-1)*cgw+bx-1]
				}
				if by > 0 {
					top = codedL[(by-1)*cgw+bx]
				}
				pred := top
				if topLeft == top {
					pred = left
				}
				cbp6[i] ^= pred
				codedL[by*cgw+bx] = cbp6[i]
			}
			acPred := r.bit() == 1
			if perMBRL {
				cbpAny := false
				for _, c := range cbp6 {
					cbpAny = cbpAny || c != 0
				}
				if cbpAny {
					rl := r.c3()
					lumaSet, chromaSet = lumaSets[rl], chromaSets[rl]
					lumaMR, chromaMR = lumaMRs[rl], chromaMRs[rl]
					if lumaSet == nil || chromaSet == nil {
						return nil, errUnsupportedConfig
					}
				}
			}
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

				var qf [64]int
				qf[0] = lev

				cbp := cbp6[blk]
				if cbp != 0 {
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
						ts, mr = chromaSet, chromaMR
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
				px := idct8(&coeff)
				writeBlock(blk, mx, my, cw, px, yPlane, cbPlane, crPlane)
			}
		}
	}
	if loopFilter {
		applyH263LoopFilter(yPlane, cbPlane, crPlane, cw, ch, mbw, mbh, q)
	}
	copyPlane(img.Y, yPlane, img.YStride, cw, w, h)
	copyPlane(img.Cb, cbPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	copyPlane(img.Cr, crPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	return img, nil
}
