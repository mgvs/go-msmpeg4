package msmpeg4

// Standard integer inverse DCT (the classic 8-bit MPEG/JPEG integer IDCT) used by msmpeg4 v1–v3
// and WMV1, replacing the shared float IDCT for bit-exact reconstruction. WMV2 uses its own wmv2IDCT.

const (
	siW1       = 22725
	siW2       = 21407
	siW3       = 19266
	siW4       = 16383
	siW5       = 12873
	siW6       = 8867
	siW7       = 4520
	siRowSh    = 11
	siColSh    = 20
	siColRound = (1 << (siColSh - 1)) / siW4
)

func simpleIDCTRow(r []int) {
	a0 := siW4*r[0] + (1 << (siRowSh - 1))
	a1, a2, a3 := a0, a0, a0
	a0 += siW2 * r[2]
	a1 += siW6 * r[2]
	a2 -= siW6 * r[2]
	a3 -= siW2 * r[2]
	b0 := siW1*r[1] + siW3*r[3]
	b1 := siW3*r[1] - siW7*r[3]
	b2 := siW5*r[1] - siW1*r[3]
	b3 := siW7*r[1] - siW5*r[3]
	a0 += siW4*r[4] + siW6*r[6]
	a1 += -siW4*r[4] - siW2*r[6]
	a2 += -siW4*r[4] + siW2*r[6]
	a3 += siW4*r[4] - siW6*r[6]
	b0 += siW5*r[5] + siW7*r[7]
	b1 += -siW1*r[5] - siW5*r[7]
	b2 += siW7*r[5] + siW3*r[7]
	b3 += siW3*r[5] - siW1*r[7]
	r[0] = (a0 + b0) >> siRowSh
	r[7] = (a0 - b0) >> siRowSh
	r[1] = (a1 + b1) >> siRowSh
	r[6] = (a1 - b1) >> siRowSh
	r[2] = (a2 + b2) >> siRowSh
	r[5] = (a2 - b2) >> siRowSh
	r[3] = (a3 + b3) >> siRowSh
	r[4] = (a3 - b3) >> siRowSh
}

func simpleIDCTCol(c []int) {
	a0 := siW4 * (c[8*0] + siColRound)
	a1, a2, a3 := a0, a0, a0
	a0 += siW2 * c[8*2]
	a1 += siW6 * c[8*2]
	a2 -= siW6 * c[8*2]
	a3 -= siW2 * c[8*2]
	b0 := siW1 * c[8*1]
	b1 := siW3 * c[8*1]
	b2 := siW5 * c[8*1]
	b3 := siW7 * c[8*1]
	b0 += siW3 * c[8*3]
	b1 += -siW7 * c[8*3]
	b2 += -siW1 * c[8*3]
	b3 += -siW5 * c[8*3]
	a0 += siW4 * c[8*4]
	a1 += -siW4 * c[8*4]
	a2 += -siW4 * c[8*4]
	a3 += siW4 * c[8*4]
	b0 += siW5 * c[8*5]
	b1 += -siW1 * c[8*5]
	b2 += siW7 * c[8*5]
	b3 += siW3 * c[8*5]
	a0 += siW6 * c[8*6]
	a1 += -siW2 * c[8*6]
	a2 += siW2 * c[8*6]
	a3 += -siW6 * c[8*6]
	b0 += siW7 * c[8*7]
	b1 += -siW5 * c[8*7]
	b2 += siW3 * c[8*7]
	b3 += -siW1 * c[8*7]
	c[8*0] = (a0 + b0) >> siColSh
	c[8*1] = (a1 + b1) >> siColSh
	c[8*2] = (a2 + b2) >> siColSh
	c[8*3] = (a3 + b3) >> siColSh
	c[8*4] = (a3 - b3) >> siColSh
	c[8*5] = (a2 - b2) >> siColSh
	c[8*6] = (a1 - b1) >> siColSh
	c[8*7] = (a0 - b0) >> siColSh
}

func simpleIDCT(block *[64]int) {
	for i := 0; i < 64; i += 8 {
		simpleIDCTRow(block[i : i+8])
	}
	for i := 0; i < 8; i++ {
		simpleIDCTCol(block[i:])
	}
}

// simpleResidual runs the integer simple IDCT over the (integer-valued) dequantized coefficients.
func simpleResidual(coeff *[64]float64) [64]float64 {
	var b [64]int
	for i, c := range coeff {
		b[i] = int(c)
	}
	simpleIDCT(&b)
	var out [64]float64
	for i, v := range b {
		out[i] = float64(v)
	}
	return out
}
