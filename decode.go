package msmpeg4

import (
	"errors"
	"image"
)

// zigZagUV maps scan position → (row,col) in the 8×8 block (standard zigzag).
var zigZagUV = [64][2]int{
	{0, 0}, {0, 1}, {1, 0}, {2, 0}, {1, 1}, {0, 2}, {0, 3}, {1, 2}, {2, 1}, {3, 0},
	{4, 0}, {3, 1}, {2, 2}, {1, 3}, {0, 4}, {0, 5}, {1, 4}, {2, 3}, {3, 2}, {4, 1},
	{5, 0}, {6, 0}, {5, 1}, {4, 2}, {3, 3}, {2, 4}, {1, 5}, {0, 6}, {0, 7}, {1, 6},
	{2, 5}, {3, 4}, {4, 3}, {5, 2}, {6, 1}, {7, 0}, {7, 1}, {6, 2}, {5, 3}, {4, 4},
	{3, 5}, {2, 6}, {1, 7}, {2, 7}, {3, 6}, {4, 5}, {5, 4}, {6, 3}, {7, 2}, {7, 3},
	{6, 4}, {5, 5}, {4, 6}, {3, 7}, {4, 7}, {5, 6}, {6, 5}, {7, 4}, {7, 5}, {6, 6},
	{5, 7}, {6, 7}, {7, 6}, {7, 7},
}

var errDecode = errors.New("msmpeg4: intra decode failed (likely an unreversed case: last=0 / AC prediction)")

// errUnsupportedConfig is returned when the frame selects RL/DC tables that have not
// yet been reversed (only rl_table=2 / rl_chroma=1 / dc_table=1 are currently available).
var errUnsupportedConfig = errors.New("msmpeg4: unsupported table config (rl_table or rl_chroma_table index not yet reversed)")

// c3 reads the 3-valued table-index VLC: "0"->0, "10"->1, "11"->2.
func (r *bitReader) c3() int {
	if r.bit() == 0 {
		return 0
	}
	if r.bit() == 0 {
		return 1
	}
	return 2
}

// Scan tables as raster indices (row*8+col) per scan position. zigzag is the default;
// the two alternate scans are selected when AC prediction is active (MPEG-4 §7.4: the
// alternate-vertical scan is used when predicting from the horizontally adjacent block,
// alternate-horizontal when predicting from the block above).
var scanZigzag = func() [64]int {
	var s [64]int
	for k := 0; k < 64; k++ {
		s[k] = zigZagUV[k][0]*8 + zigZagUV[k][1]
	}
	return s
}()

var scanAltHoriz = [64]int{
	0, 1, 2, 3, 8, 9, 16, 17, 10, 11, 4, 5, 6, 7, 15, 14,
	13, 12, 19, 18, 24, 25, 32, 33, 26, 27, 20, 21, 22, 23, 28, 29,
	30, 31, 34, 35, 40, 41, 48, 49, 42, 43, 36, 37, 38, 39, 44, 45,
	46, 47, 50, 51, 56, 57, 58, 59, 52, 53, 54, 55, 60, 61, 62, 63,
}

var scanAltVert = [64]int{
	0, 8, 16, 24, 1, 9, 2, 10, 17, 25, 32, 40, 48, 56, 57, 49,
	41, 33, 26, 18, 3, 11, 4, 12, 19, 27, 34, 42, 50, 58, 35, 43,
	51, 59, 20, 28, 5, 13, 6, 14, 21, 29, 36, 44, 52, 60, 37, 45,
	53, 61, 22, 30, 7, 15, 23, 31, 38, 46, 54, 62, 39, 47, 55, 63,
}

// acGrid stores per-block quantised AC coefficients needed for AC prediction: the first
// row QF[0][1..7] and first column QF[1..7][0] of every decoded intra block.
type acGrid struct {
	w   int
	row [][8]int // first row: [block][col 0..7] (index 0 unused / DC)
	col [][8]int // first column: [block][row 0..7]
}

func newACGrid(w, h int) *acGrid {
	return &acGrid{w: w, row: make([][8]int, w*h), col: make([][8]int, w*h)}
}

// dcGrid tracks per-block DC levels for the MPEG-4 gradient predictor.
type dcGrid struct {
	w, h int
	v    []int
}

func newDCGrid(w, h, def int) *dcGrid {
	g := &dcGrid{w: w, h: h, v: make([]int, w*h)}
	for i := range g.v {
		g.v[i] = def
	}
	return g
}

func (g *dcGrid) set(x, y, v int) { g.v[y*g.w+x] = v }

// predictDC returns the DC predictor for cell (x,y) and the gradient direction
// (fromLeft = predict from the left neighbour A, else from the top neighbour C).
func (g *dcGrid) predictDC(x, y, def int, acPred, topRow bool) (pred int, fromLeft bool) {
	a, b, c := def, def, def // left, top-left, top
	if x > 0 {
		a = g.v[y*g.w+x-1]
	}
	if x > 0 && y > 0 {
		b = g.v[(y-1)*g.w+x-1]
	}
	if y > 0 {
		c = g.v[(y-1)*g.w+x]
	}
	fromLeft = abs(a-b) > abs(b-c)
	if fromLeft {
		pred = a
	} else {
		pred = c
	}
	return
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

// DecodeIntraFrame decodes one MS-MPEG4 v3 intra frame (raw video elementary
// stream for the frame) of the given dimensions into a 4:2:0 image.
//
// It handles: per-MB joint-MCBPC prefix + gradient-predicted DC (±127) + AC
// (TCOEF + ESC1/2/3) + adaptive AC prediction (§7.3.3.3: direction from the DC
// gradient, alternate scan, first row/column added from the neighbour block) +
// dequant + IDCT. INTRA+Q (type-4 MB) is not yet handled.
func DecodeIntraFrame(data []byte, w, h int) (*image.YCbCr, error) {
	r := newBitReader(data)
	r.u(2) // coding type (00 = intra picture)
	q := r.u(5)
	if q == 0 {
		return nil, errDecode
	}
	r.u(5)           // remaining fixed picture-header bits (before the table indices)
	rcIdx := r.c3()  // rl_chroma_table_index
	rtIdx := r.c3()  // rl_table_index
	dcIdx := r.bit() // dc_table_index
	lumaSet := lumaTCOEF[rtIdx]
	chromaSet := chromaTCOEF[rcIdx]
	if lumaSet == nil || chromaSet == nil {
		return nil, errUnsupportedConfig
	}
	dcLuma := dcTables[dcIdx][0]
	dcChro := dcTables[dcIdx][1]
	// MPEG-4 intra DC scaler (Part 2 §7.4.4): luma and chroma use different formulas.
	// Default predictor for unavailable neighbours: round(1024/dc_scaler).
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

	gL := newDCGrid(2*mbw, 2*mbh, defL)
	gCb := newDCGrid(mbw, mbh, defC)
	gCr := newDCGrid(mbw, mbh, defC)
	acL := newACGrid(2*mbw, 2*mbh)
	acCb := newACGrid(mbw, mbh)
	acCr := newACGrid(mbw, mbh)

	// coded-block grid for luma CBP prediction (one cell per 8×8 luma block).
	cgw := 2 * mbw
	codedL := make([]int, cgw*2*mbh)

	for my := 0; my < mbh; my++ {
		for mx := 0; mx < mbw; mx++ {
			cbp6, ok := r.decodeMCBPC()
			if !ok {
				return nil, errDecode
			}
			// CBP prediction for the 4 luma blocks: cbp = raw XOR pred, where
			// pred = (topLeft==top) ? left : top, using the coded-grid. The update is
			// incremental within the MB (block 0 is block 1's left neighbour, etc.).
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
			// AC-prediction flag is read once per MB (before any block), used for all blocks.
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

				// qf holds quantised coefficients (raster order); AC prediction operates here.
				var qf [64]int
				qf[0] = lev

				cbp := cbp6[blk]
				if cbp != 0 {
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
						if n >= 64 { // a block has at most 63 AC coefficients; bail on malformed input
							return nil, errDecode
						}
						ts := lumaSet
						if blk >= 4 {
							ts = chromaSet
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
				if acPred {
					// AC prediction applies regardless of cbp: when predicting from
					// the left, add the left neighbour's first column to our first
					// column; when predicting from above, add the top neighbour's
					// first row to our first row.
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

				// Store this block's reconstructed first row/column for later predictors.
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
	copyPlane(img.Y, yPlane, img.YStride, cw, w, h)
	copyPlane(img.Cb, cbPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	copyPlane(img.Cr, crPlane, img.CStride, cw/2, (w+1)/2, (h+1)/2)
	return img, nil
}

// intraDCScaler returns the MPEG-4 Part 2 intra DC scaler for luminance (§7.4.4).
func intraDCScaler(q int) int {
	switch {
	case q <= 4:
		return 8
	case q <= 8:
		return 2 * q
	case q <= 24:
		return q + 8
	default:
		return 2*q - 16
	}
}

// chromaIntraDCScaler returns the MPEG-4 Part 2 intra DC scaler for chroma (§7.4.4).
func chromaIntraDCScaler(q int) int {
	switch {
	case q <= 4:
		return 8
	case q <= 24:
		return (q + 13) / 2
	default:
		return q - 6
	}
}

// decodeMCBPC reads the per-MB joint MCBPCY prefix and returns the 6-block coded-block
// pattern [y0,y1,y2,y3,cb,cr]. INTRA (type 3) only for now.
func (r *bitReader) decodeMCBPC() ([6]int, bool) {
	code, n := 0, 0
	for n < 14 {
		code = code<<1 | r.bit()
		n++
		for _, e := range mcbpcVLC {
			if e.length == n && e.code == code {
				return [6]int{e.y0, e.y1, e.y2, e.y3, e.cb, e.cr}, true
			}
		}
	}
	return [6]int{}, false
}

// dequantAC: |coef| = q·(2·|level|+1), minus 1 when q is even (oddification, H.263 §6.2.1).
func dequantAC(level, q int) float64 {
	if level == 0 {
		return 0
	}
	v := q * (2*abs(level) + 1)
	if q%2 == 0 {
		v--
	}
	if level < 0 {
		v = -v
	}
	return float64(v)
}

func writeBlock(blk, mx, my, cw int, px [64]float64, y, cb, cr []byte) {
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
			dst[(r0+i)*stride+c0+j] = clampByte(px[i*8+j])
		}
	}
}

func copyPlane(dst, src []byte, dstStride, srcStride, w, h int) {
	for y := 0; y < h; y++ {
		copy(dst[y*dstStride:y*dstStride+w], src[y*srcStride:y*srcStride+w])
	}
}
