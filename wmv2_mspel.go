package msmpeg4

// WMV2 "ms-pel" motion compensation. When the frame's mspel flag is set, luma MC uses a special
// half-pel interpolation (an 8-tap lowpass), selected by dxy = 2*((my&1)<<1 | (mx&1)) + hshift
// (8 positions). Chroma uses standard H.263 half-pel at MV>>2 with a fractional flag. Taps and
// dispatch follow the open WMV2 spec.

func clamp255(v int) int {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return v
}

func mspelRef(p []byte, stride, w, h, x, y int) int {
	if x < 0 {
		x = 0
	} else if x >= w {
		x = w - 1
	}
	if y < 0 {
		y = 0
	} else if y >= h {
		y = h - 1
	}
	return int(p[y*stride+x])
}

// hLow8 / vLow8 apply the lowpass filter cm[(9*(a+b) - (c+d) + 8) >> 4] producing an 8-wide row
// (h) or processing a vertical column. They read the reference with edge clamping.
func mspelHLowRow(p []byte, stride, w, h, x0, y int, out []int) {
	for i := 0; i < 8; i++ {
		a := mspelRef(p, stride, w, h, x0+i, y)
		b := mspelRef(p, stride, w, h, x0+i+1, y)
		c := mspelRef(p, stride, w, h, x0+i-1, y)
		d := mspelRef(p, stride, w, h, x0+i+2, y)
		out[i] = clamp255((9*(a+b) - (c + d) + 8) >> 4)
	}
}

func mspelVLowFromRef(p []byte, stride, w, h, x0, y0 int, out *[8][8]int) {
	for j := 0; j < 8; j++ { // column
		for i := 0; i < 8; i++ { // row
			a := mspelRef(p, stride, w, h, x0+j, y0+i)
			b := mspelRef(p, stride, w, h, x0+j, y0+i+1)
			c := mspelRef(p, stride, w, h, x0+j, y0+i-1)
			d := mspelRef(p, stride, w, h, x0+j, y0+i+2)
			out[i][j] = clamp255((9*(a+b) - (c + d) + 8) >> 4)
		}
	}
}

// vLowBuf applies the vertical lowpass to an intermediate buffer `buf` (rows×8) whose first used
// row is `r0`, producing an 8×8 output.
func mspelVLowBuf(buf [][8]int, r0 int, out *[8][8]int) {
	for j := 0; j < 8; j++ {
		for i := 0; i < 8; i++ {
			a := buf[r0+i][j]
			b := buf[r0+i+1][j]
			c := buf[r0+i-1][j]
			d := buf[r0+i+2][j]
			out[i][j] = clamp255((9*(a+b) - (c + d) + 8) >> 4)
		}
	}
}

func avg8(a, b *[8][8]int, out *[8][8]int, noRound bool) {
	rnd := 1
	if noRound {
		rnd = 0
	}
	for i := 0; i < 8; i++ {
		for j := 0; j < 8; j++ {
			out[i][j] = (a[i][j] + b[i][j] + rnd) >> 1
		}
	}
}

// hLowBlock returns rows×8 of the horizontal lowpass starting at (x0,y0).
func mspelHLowBlock(p []byte, stride, w, h, x0, y0, rows int) [][8]int {
	buf := make([][8]int, rows)
	for r := 0; r < rows; r++ {
		var row [8]int
		mspelHLowRow(p, stride, w, h, x0, y0+r, row[:])
		buf[r] = row
	}
	return buf
}

// mspelLuma8 computes one 8×8 luma MC block at integer source (sx,sy) with sub-pel position dxy.
func mspelLuma8(p []byte, stride, w, h, sx, sy, dxy int, noRound bool) [64]int {
	var o [8][8]int
	switch dxy {
	case 0: // full pixel
		for i := 0; i < 8; i++ {
			for j := 0; j < 8; j++ {
				o[i][j] = mspelRef(p, stride, w, h, sx+j, sy+i)
			}
		}
	case 1: // mc10: avg(src, hLow(src))
		var half, full [8][8]int
		for i := 0; i < 8; i++ {
			var r [8]int
			mspelHLowRow(p, stride, w, h, sx, sy+i, r[:])
			for j := 0; j < 8; j++ {
				half[i][j] = r[j]
				full[i][j] = mspelRef(p, stride, w, h, sx+j, sy+i)
			}
		}
		avg8(&full, &half, &o, noRound)
	case 2: // mc20: hLow(src)
		for i := 0; i < 8; i++ {
			var r [8]int
			mspelHLowRow(p, stride, w, h, sx, sy+i, r[:])
			for j := 0; j < 8; j++ {
				o[i][j] = r[j]
			}
		}
	case 3: // mc30: avg(src+1, hLow(src))
		var half, full [8][8]int
		for i := 0; i < 8; i++ {
			var r [8]int
			mspelHLowRow(p, stride, w, h, sx, sy+i, r[:])
			for j := 0; j < 8; j++ {
				half[i][j] = r[j]
				full[i][j] = mspelRef(p, stride, w, h, sx+j+1, sy+i)
			}
		}
		avg8(&full, &half, &o, noRound)
	case 4: // mc02: vLow(src)
		mspelVLowFromRef(p, stride, w, h, sx, sy, &o)
	case 5: // mc12: avg(vLow(src), vLow(hLow(src-stride)+row1))
		var hv, v [8][8]int
		mspelVLowFromRef(p, stride, w, h, sx, sy, &v)
		buf := mspelHLowBlock(p, stride, w, h, sx, sy-1, 11)
		mspelVLowBuf(buf, 1, &hv)
		avg8(&v, &hv, &o, noRound)
	case 6: // mc22: vLow(hLow(src-stride)+row1)
		buf := mspelHLowBlock(p, stride, w, h, sx, sy-1, 11)
		mspelVLowBuf(buf, 1, &o)
	default: // 7 mc32: avg(vLow(src+1), vLow(hLow(src-stride)+row1))
		var hv, v [8][8]int
		mspelVLowFromRef(p, stride, w, h, sx+1, sy, &v)
		buf := mspelHLowBlock(p, stride, w, h, sx, sy-1, 11)
		mspelVLowBuf(buf, 1, &hv)
		avg8(&v, &hv, &o, noRound)
	}
	var out [64]int
	for i := 0; i < 8; i++ {
		for j := 0; j < 8; j++ {
			out[i*8+j] = o[i][j]
		}
	}
	return out
}

// mspelLumaBlock fills the 8×8 MC for luma block `blk` of MB (mx,my) under mspel, MV (mvx,mvy)
// half-pel and the per-MB hshift.
func mspelLumaBlock(out []int, ref []byte, stride, w, h, mx, my, blk, mvx, mvy, hshift int, noRound bool) {
	dxy := 2*(((mvy&1)<<1)|(mvx&1)) + hshift
	sx := mx*16 + (mvx >> 1) + (blk%2)*8
	sy := my*16 + (mvy >> 1) + (blk/2)*8
	b := mspelLuma8(ref, stride, w, h, sx, sy, dxy, noRound)
	copy(out, b[:])
}

// mspelChromaBlock fills the 8×8 chroma MC under mspel: standard half-pel at MV>>2 with a
// fractional flag (rounded averaging).
func mspelChromaBlock(out []int, ref []byte, stride, cw, ch, mx, my, mvx, mvy int, noRound bool) {
	r2, r4 := 1, 2
	if noRound {
		r2, r4 = 0, 1
	}
	dxy := 0
	if mvx&3 != 0 {
		dxy |= 1
	}
	if mvy&3 != 0 {
		dxy |= 2
	}
	sx := mx*8 + (mvx >> 2)
	sy := my*8 + (mvy >> 2)
	for i := 0; i < 8; i++ {
		for j := 0; j < 8; j++ {
			a := mspelRef(ref, stride, cw, ch, sx+j, sy+i)
			var v int
			switch dxy {
			case 0:
				v = a
			case 1:
				v = (a + mspelRef(ref, stride, cw, ch, sx+j+1, sy+i) + r2) >> 1
			case 2:
				v = (a + mspelRef(ref, stride, cw, ch, sx+j, sy+i+1) + r2) >> 1
			default:
				b := mspelRef(ref, stride, cw, ch, sx+j+1, sy+i)
				c := mspelRef(ref, stride, cw, ch, sx+j, sy+i+1)
				d := mspelRef(ref, stride, cw, ch, sx+j+1, sy+i+1)
				v = (a + b + c + d + r4) >> 2
			}
			out[i*8+j] = v
		}
	}
}
