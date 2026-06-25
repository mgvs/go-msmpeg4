package msmpeg4

import "image"

// decodeInterBlockV1 decodes one 8×8 inter block for v1 (plain ISO escape, zigzag scan).
func (r *bitReader) decodeInterBlockV1(q int, t *tcoefTableSet) ([64]float64, bool) {
	var qf [64]int
	pos := 0
	for n := 0; ; n++ {
		if n >= 64 {
			return [64]float64{}, false
		}
		c, ok := r.decodeTCOEFv1(t)
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

// DecodePFrameV1 decodes one MS-MPEG4 v1 P-frame given the previously decoded reference frame.
func DecodePFrameV1(data []byte, ref *image.YCbCr, w, h int) (*image.YCbCr, error) {
	r := newBitReader(data)
	r.u(32) // start code
	r.u(5)  // frame number
	if r.u(2) != 1 {
		return nil, errDecode // not a P-frame
	}
	q := r.u(5)
	if q == 0 || q > 31 {
		return nil, errDecode
	}
	// v1 P: use_mb_skip_code is always 1 (not coded); fixed selectors rl=2, dc-scale=8, mv=0.
	const dcScale = 8
	lumaSet := lumaTCOEF[2]
	chromaSet := chromaTCOEF[2]
	if lumaSet == nil || chromaSet == nil {
		return nil, errUnsupportedConfig
	}

	mbw, mbh := (w+15)/16, (h+15)/16
	cw, ch := mbw*16, mbh*16
	img := image.NewYCbCr(image.Rect(0, 0, w, h), image.YCbCrSubsampleRatio420)
	yPlane := make([]byte, cw*ch)
	cbPlane := make([]byte, (cw/2)*(ch/2))
	crPlane := make([]byte, (cw/2)*(ch/2))
	copyPlane(yPlane, ref.Y, cw, ref.YStride, w, h)
	copyPlane(cbPlane, ref.Cb, cw/2, ref.CStride, (w+1)/2, (h+1)/2)
	copyPlane(crPlane, ref.Cr, cw/2, ref.CStride, (w+1)/2, (h+1)/2)

	type mv2 struct{ x, y int }
	mvGrid := make([]mv2, mbw*mbh)

	const dcPredInit = 1024 / dcScale
	for my := 0; my < mbh; my++ {
		predY, predCb, predCr := dcPredInit, dcPredInit, dcPredInit
		for mx := 0; mx < mbw; mx++ {
			if r.bit() == 1 { // use_mb_skip_code=1 always → skip bit per MB
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
				// v1 MV predictor: the left neighbour only (0 at the left edge) — NOT the
				// H.263/MPEG-4 median of left/top/top-right.
				var predx, predy int
				if mx > 0 {
					predx = mvGrid[my*mbw+mx-1].x
					predy = mvGrid[my*mbw+mx-1].y
				}
				mvx, ok1 := r.decodeV2MV(predx)
				mvy, ok2 := r.decodeV2MV(predy)
				if !ok1 || !ok2 {
					return nil, errDecode
				}
				mvGrid[my*mbw+mx] = mv2{mvx, mvy}

				cmvx := (mvx >> 1) | (mvx & 1)
				cmvy := (mvy >> 1) | (mvy & 1)
				for blk := 0; blk < 6; blk++ {
					coded := (cbp>>(5-blk))&1 == 1
					var mcBuf [64]int
					if blk < 4 {
						r0 := my*16 + (blk/2)*8
						c0 := mx*16 + (blk%2)*8
						mcFill(mcBuf[:], ref.Y, ref.YStride, w, h, r0, c0, mvx, mvy, false)
					} else {
						r0 := my * 8
						c0 := mx * 8
						chRef := ref.Cb
						if blk == 5 {
							chRef = ref.Cr
						}
						mcFill(mcBuf[:], chRef, ref.CStride, (w+1)/2, (h+1)/2, r0, c0, cmvx, cmvy, false)
					}
					if coded {
						coeff, ok := r.decodeInterBlockV1(q, chromaSet)
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
				// --- INTRA MB in v1 P-frame (H.263 CBPY, MPEG-1 DC prediction) ---
				mvGrid[my*mbw+mx] = mv2{0, 0}
				cbpyV, ok := r.decodeH263CBPY()
				if !ok {
					return nil, errDecode
				}
				cbp := (cbpyV << 2) | cbpc
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
	}
	copyPlane(img.Y, yPlane, img.YStride, cw, w, h)
	copyPlane(img.Cb, cbPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	copyPlane(img.Cr, crPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	return img, nil
}
