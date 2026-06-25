package msmpeg4

import "math"

// 8×8 inverse DCT (float reference). cosTab[u][x] = (u==0 ? 1/√2 : 1)·cos((2x+1)uπ/16)/2.
var cosTab = func() [8][8]float64 {
	var m [8][8]float64
	for u := 0; u < 8; u++ {
		cu := 1.0
		if u == 0 {
			cu = 1 / math.Sqrt2
		}
		for x := 0; x < 8; x++ {
			m[u][x] = 0.5 * cu * math.Cos((2*float64(x)+1)*float64(u)*math.Pi/16)
		}
	}
	return m
}()

// idct8 computes the inverse DCT of an 8×8 coefficient block into spatial samples.
func idct8(in *[64]float64) [64]float64 {
	var tmp [64]float64 // columns: out = cosᵀ · in
	for x := 0; x < 8; x++ {
		for v := 0; v < 8; v++ {
			var s float64
			for u := 0; u < 8; u++ {
				s += cosTab[u][x] * in[u*8+v]
			}
			tmp[x*8+v] = s
		}
	}
	var out [64]float64 // rows: out = tmp · cos
	for x := 0; x < 8; x++ {
		for y := 0; y < 8; y++ {
			var s float64
			for v := 0; v < 8; v++ {
				s += tmp[x*8+v] * cosTab[v][y]
			}
			out[x*8+y] = s
		}
	}
	return out
}

func clampByte(v float64) byte {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return byte(v + 0.5)
}
