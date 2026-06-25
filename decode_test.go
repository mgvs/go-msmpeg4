package msmpeg4

import "testing"

// m4Frame is a crafted 16×16 DIV3 intra frame whose four 8×8 luma blocks are flat
// at 128, 160, 144, 176 — exercising the picture header, per-MB prefix, gradient DC
// prediction, dequant and IDCT. ffmpeg decodes it to exactly those block means.
var m4Frame = []byte{17, 123, 210, 64, 0, 1, 145, 135}

func TestDecodeM4(t *testing.T) {
	img, err := DecodeIntraFrame(m4Frame, 16, 16)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	want := [4][3]int{{0, 0, 128}, {0, 8, 160}, {8, 0, 144}, {8, 8, 176}}
	for _, w := range want {
		r0, c0, exp := w[0], w[1], w[2]
		sum := 0
		for i := 0; i < 8; i++ {
			for j := 0; j < 8; j++ {
				sum += int(img.Y[(r0+i)*img.YStride+c0+j])
			}
		}
		got := sum / 64
		if got != exp {
			t.Errorf("luma block (%d,%d): mean=%d want %d", r0, c0, got, exp)
		}
	}
	t.Log("m4 frame: 4 luma blocks decode to 128/160/144/176 (header+prefix+gradient DC+IDCT)")
}

// Round-trip the DC tables and TCOEF stay intact alongside the decoder.
func TestTablesPresent(t *testing.T) {
	for dct := 0; dct < 2; dct++ {
		for ch := 0; ch < 2; ch++ {
			if dcTables[dct][ch] == nil || len(dcTables[dct][ch].vlc) != 120 {
				t.Fatalf("dcTables[%d][%d]: got %d entries, want 120", dct, ch,
					func() int {
						if dcTables[dct][ch] == nil {
							return 0
						}
						return len(dcTables[dct][ch].vlc)
					}())
			}
		}
	}
	if len(tcoefLumaVLC) != 102 || len(tcoefChromaVLC) != 168 || len(cbpyTable) != 16 {
		t.Fatalf("tcoef luma=%d chroma=%d cbpy=%d", len(tcoefLumaVLC), len(tcoefChromaVLC), len(cbpyTable))
	}
}
