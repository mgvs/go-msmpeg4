package msmpeg4

// H.263 in-loop deblocking filter, used by WMV2 when its extradata loop_filter flag is set.
// Tables and tap formulas are the open ITU-T H.263 deblocking specification (Annex J). For an
// intra frame the prediction is coefficient-domain, so the filter is applied as a post-process
// over the reconstructed (MB-padded) planes in macroblock raster order.

var h263LoopStrength = [32]int{
	0, 1, 1, 2, 2, 3, 3, 4, 4, 4, 5, 5, 6, 6, 7, 7,
	7, 8, 8, 8, 9, 9, 9, 10, 10, 10, 11, 11, 11, 12, 12, 12,
}

var h263ChromaQscale = [32]int{
	0, 1, 2, 3, 4, 5, 6, 6, 7, 8, 9, 9, 10, 10, 11, 11,
	12, 12, 12, 13, 13, 13, 14, 14, 14, 14, 14, 15, 15, 15, 15, 15,
}

func lfClamp(v int) int {
	if v&256 != 0 {
		if v < 0 {
			return 0
		}
		return 255
	}
	return v
}

func lfClip(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func lfCore(p0, p1, p2, p3, strength int) (np0, np1, np2, np3 int) {
	d := (p0 - p3 + 4*(p2-p1)) / 8
	var d1 int
	switch {
	case d < -2*strength:
		d1 = 0
	case d < -strength:
		d1 = -2*strength - d
	case d < strength:
		d1 = d
	case d < 2*strength:
		d1 = 2*strength - d
	default:
		d1 = 0
	}
	p1 = lfClamp(p1 + d1)
	p2 = lfClamp(p2 - d1)
	ad1 := d1
	if ad1 < 0 {
		ad1 = -ad1
	}
	ad1 >>= 1
	d2 := lfClip((p0-p3)/4, -ad1, ad1)
	return (p0 - d2) & 0xFF, p1, p2, (p3 + d2) & 0xFF
}

// h263VLoop filters a horizontal block edge (between rows off-1 and off), 8 columns wide.
func h263VLoop(p []byte, off, stride, strength int) {
	for x := 0; x < 8; x++ {
		i := off + x
		n0, n1, n2, n3 := lfCore(int(p[i-2*stride]), int(p[i-stride]), int(p[i]), int(p[i+stride]), strength)
		p[i-2*stride], p[i-stride], p[i], p[i+stride] = byte(n0), byte(n1), byte(n2), byte(n3)
	}
}

// h263HLoop filters a vertical block edge (between columns off-1 and off), 8 rows tall.
func h263HLoop(p []byte, off, stride, strength int) {
	for y := 0; y < 8; y++ {
		i := off + y*stride
		n0, n1, n2, n3 := lfCore(int(p[i-2]), int(p[i-1]), int(p[i]), int(p[i+1]), strength)
		p[i-2], p[i-1], p[i], p[i+1] = byte(n0), byte(n1), byte(n2), byte(n3)
	}
}

// applyH263LoopFilter deblocks an intra frame (uniform qscale, no skipped MBs) over the padded
// planes, following the H.263 Annex J per-MB edge order so each edge is filtered once.
func applyH263LoopFilter(y, cb, cr []byte, cw, ch, mbw, mbh, q int) {
	ls := cw
	uvls := cw / 2
	sL := h263LoopStrength[q]
	sC := h263LoopStrength[h263ChromaQscale[q]]
	for my := 0; my < mbh; my++ {
		for mx := 0; mx < mbw; mx++ {
			dy := my*16*ls + mx*16
			dc := my*8*uvls + mx*8
			// internal horizontal edge (row 8)
			h263VLoop(y, dy+8*ls, ls, sL)
			h263VLoop(y, dy+8*ls+8, ls, sL)

			if my > 0 {
				// top edge (row 0) — luma + chroma
				h263VLoop(y, dy, ls, sL)
				h263VLoop(y, dy+8, ls, sL)
				h263VLoop(cb, dc, uvls, sC)
				h263VLoop(cr, dc, uvls, sC)
				// internal vertical edge of the MB above (col 8)
				h263HLoop(y, dy-8*ls+8, ls, sL)
				if mx > 0 {
					h263HLoop(y, dy-8*ls, ls, sL)
					h263HLoop(cb, dc-8*uvls, uvls, sC)
					h263HLoop(cr, dc-8*uvls, uvls, sC)
				}
			}
			// internal vertical edge (col 8)
			h263HLoop(y, dy+8, ls, sL)
			if my+1 == mbh {
				h263HLoop(y, dy+8*ls+8, ls, sL)
			}
			if mx > 0 {
				// left edge (col 0)
				h263HLoop(y, dy, ls, sL)
				if my+1 == mbh {
					h263HLoop(y, dy+8*ls, ls, sL)
					h263HLoop(cb, dc, uvls, sC)
					h263HLoop(cr, dc, uvls, sC)
				}
			}
		}
	}
}
