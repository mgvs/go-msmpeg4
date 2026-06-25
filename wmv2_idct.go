package msmpeg4

// WMV2 integer inverse DCT (the exact transform WMV2 uses, replacing the shared float IDCT for
// bit-exact reconstruction). The W constants are the open MPEG IDCT values (2048·√2·cos(kπ/16)).

const (
	wW0 = 2048
	wW1 = 2841
	wW2 = 2676
	wW3 = 2408
	wW4 = 2048
	wW5 = 1609
	wW6 = 1108
	wW7 = 565
)

func wmv2IDCTRow(b []int) {
	a1 := wW1*b[1] + wW7*b[7]
	a7 := wW7*b[1] - wW1*b[7]
	a5 := wW5*b[5] + wW3*b[3]
	a3 := wW3*b[5] - wW5*b[3]
	a2 := wW2*b[2] + wW6*b[6]
	a6 := wW6*b[2] - wW2*b[6]
	a0 := wW0*b[0] + wW0*b[4]
	a4 := wW0*b[0] - wW0*b[4]
	s1 := (181*(a1-a5+a7-a3) + 128) >> 8
	s2 := (181*(a1-a5-a7+a3) + 128) >> 8
	b[0] = (a0 + a2 + a1 + a5 + (1 << 7)) >> 8
	b[1] = (a4 + a6 + s1 + (1 << 7)) >> 8
	b[2] = (a4 - a6 + s2 + (1 << 7)) >> 8
	b[3] = (a0 - a2 + a7 + a3 + (1 << 7)) >> 8
	b[4] = (a0 - a2 - a7 - a3 + (1 << 7)) >> 8
	b[5] = (a4 - a6 - s2 + (1 << 7)) >> 8
	b[6] = (a4 + a6 - s1 + (1 << 7)) >> 8
	b[7] = (a0 + a2 - a1 - a5 + (1 << 7)) >> 8
}

func wmv2IDCTCol(b []int) {
	a1 := (wW1*b[8*1] + wW7*b[8*7] + 4) >> 3
	a7 := (wW7*b[8*1] - wW1*b[8*7] + 4) >> 3
	a5 := (wW5*b[8*5] + wW3*b[8*3] + 4) >> 3
	a3 := (wW3*b[8*5] - wW5*b[8*3] + 4) >> 3
	a2 := (wW2*b[8*2] + wW6*b[8*6] + 4) >> 3
	a6 := (wW6*b[8*2] - wW2*b[8*6] + 4) >> 3
	a0 := (wW0*b[8*0] + wW0*b[8*4]) >> 3
	a4 := (wW0*b[8*0] - wW0*b[8*4]) >> 3
	s1 := (181*(a1-a5+a7-a3) + 128) >> 8
	s2 := (181*(a1-a5-a7+a3) + 128) >> 8
	b[8*0] = (a0 + a2 + a1 + a5 + (1 << 13)) >> 14
	b[8*1] = (a4 + a6 + s1 + (1 << 13)) >> 14
	b[8*2] = (a4 - a6 + s2 + (1 << 13)) >> 14
	b[8*3] = (a0 - a2 + a7 + a3 + (1 << 13)) >> 14
	b[8*4] = (a0 - a2 - a7 - a3 + (1 << 13)) >> 14
	b[8*5] = (a4 - a6 - s2 + (1 << 13)) >> 14
	b[8*6] = (a4 + a6 - s1 + (1 << 13)) >> 14
	b[8*7] = (a0 + a2 - a1 - a5 + (1 << 13)) >> 14
}

// wmv2IDCT transforms 64 dequantized coefficients in place into the spatial residual/pixels.
func wmv2IDCT(block *[64]int) {
	for i := 0; i < 64; i += 8 {
		wmv2IDCTRow(block[i : i+8])
	}
	for i := 0; i < 8; i++ {
		wmv2IDCTCol(block[i:])
	}
}
