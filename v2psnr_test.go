package msmpeg4

import (
	"math"
	"os"
	"testing"
)

// TestV2PSNRFrame192 decodes a content-rich v2 I-frame (movie11.avi
// frame 192, raw bitstream in /tmp/v2_frame.bin) and compares against the
// reference YUV (/tmp/f192.yuv, 640x480 yuv420p).
func TestV2PSNRFrame192(t *testing.T) {
	raw, err := os.ReadFile("/tmp/v2_frame.bin") // = I-frame 192
	if err != nil {
		t.Skipf("no frame: %v", err)
	}
	ref, err := os.ReadFile("/tmp/f192.yuv")
	if err != nil {
		t.Skipf("no ref: %v", err)
	}
	const w, h = 640, 480
	img, err := DecodeIntraFrameV2(raw, w, h)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	ySize := w * h
	var ymse float64
	ymax := 0
	for row := 0; row < h; row++ {
		for col := 0; col < w; col++ {
			d := int(img.Y[row*img.YStride+col]) - int(ref[row*w+col])
			if d < 0 {
				d = -d
			}
			if d > ymax {
				ymax = d
			}
			ymse += float64(d * d)
		}
	}
	ymse /= float64(ySize)
	psnr := 999.0
	if ymse > 0 {
		psnr = 10 * math.Log10(255*255/ymse)
	}
	cw, ch := w/2, h/2
	var cbmse, crmse float64
	for row := 0; row < ch; row++ {
		for col := 0; col < cw; col++ {
			d := int(img.Cb[row*img.CStride+col]) - int(ref[ySize+row*cw+col])
			cbmse += float64(d * d)
			d2 := int(img.Cr[row*img.CStride+col]) - int(ref[ySize+cw*ch+row*cw+col])
			crmse += float64(d2 * d2)
		}
	}
	cbmse /= float64(cw * ch)
	crmse /= float64(cw * ch)
	t.Logf("Y PSNR=%.1fdB MSE=%.2f maxDiff=%d | Cb MSE=%.2f Cr MSE=%.2f", psnr, ymse, ymax, cbmse, crmse)
	cy, cx := h/2, w/2
	t.Logf("center(320,240) Y=%d ref=%d", img.Y[cy*img.YStride+cx], ref[cy*w+cx])
}
