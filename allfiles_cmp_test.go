package msmpeg4

import (
	"image"
	"math"
	"os"
	"testing"
)

func TestAllFilesPixelCmp(t *testing.T) {
	cases := []struct {
		name string
		avi  string
		yuv  string
		w, h int
	}{
		{"movie5", "testdata/movie5.avi", "/tmp/movie5_frame0.yuv", 512, 384},
		{"movie1", "testdata/movie1.avi", "/tmp/movie1_frame0.yuv", 512, 288},
		{"movie2", "testdata/movie2.avi", "/tmp/movie2_frame0.yuv", 576, 240},
		{"movie3", "testdata/movie3.avi", "/tmp/movie3_frame0.yuv", 512, 354},
		{"movie4", "testdata/movie4.avi", "/tmp/movie4_frame0.yuv", 576, 256},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			frame := firstVideoFrame(t, tc.avi)
			if frame == nil {
				t.Skip("no frame")
			}
			ref, err := os.ReadFile(tc.yuv)
			if err != nil {
				t.Skipf("no ref yuv: %v", err)
			}

			img, err := DecodeIntraFrame(frame, tc.w, tc.h)
			if err != nil {
				t.Fatalf("decode: %v", err)
			}

			w, h := tc.w, tc.h
			ySize := w * h

			// Y plane MSE
			var ymse float64
			ymaxDiff := 0
			for row := 0; row < h; row++ {
				for col := 0; col < w; col++ {
					got := int(img.Y[row*img.YStride+col])
					want := int(ref[row*w+col])
					d := got - want
					if d < 0 {
						d = -d
					}
					if d > ymaxDiff {
						ymaxDiff = d
					}
					ymse += float64(d) * float64(d)
				}
			}
			ymse /= float64(w * h)

			// Cb plane
			cw, ch := w/2, h/2
			cbOff := ySize
			var cbmse float64
			for row := 0; row < ch; row++ {
				for col := 0; col < cw; col++ {
					got := int(img.Cb[row*img.CStride+col])
					want := int(ref[cbOff+row*cw+col])
					d := got - want
					cbmse += float64(d) * float64(d)
				}
			}
			cbmse /= float64(cw * ch)

			// Cr plane
			crOff := ySize + cw*ch
			var crmse float64
			for row := 0; row < ch; row++ {
				for col := 0; col < cw; col++ {
					got := int(img.Cr[row*img.CStride+col])
					want := int(ref[crOff+row*cw+col])
					d := got - want
					crmse += float64(d) * float64(d)
				}
			}
			crmse /= float64(cw * ch)

			psnrY := 10 * math.Log10(255*255/ymse)
			if ymse == 0 {
				psnrY = 999
			}
			t.Logf("Y  MSE=%.2f PSNR=%.1fdB maxDiff=%d", ymse, psnrY, ymaxDiff)
			t.Logf("Cb MSE=%.2f", cbmse)
			t.Logf("Cr MSE=%.2f", crmse)
		})
	}
}

func planeMSE(got []byte, gotStride int, ref []byte, w, h int) (mse float64, maxDiff int) {
	for row := 0; row < h; row++ {
		for col := 0; col < w; col++ {
			d := int(got[row*gotStride+col]) - int(ref[row*w+col])
			if d < 0 {
				d = -d
			}
			if d > maxDiff {
				maxDiff = d
			}
			mse += float64(d) * float64(d)
		}
	}
	mse /= float64(w * h)
	return
}

// loadYCbCr constructs an image.YCbCr from a raw I420 YUV file.
func loadYCbCr(t *testing.T, path string, w, h int) *image.YCbCr {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("no file %s: %v", path, err)
	}
	img := image.NewYCbCr(image.Rect(0, 0, w, h), image.YCbCrSubsampleRatio420)
	for row := 0; row < h; row++ {
		for col := 0; col < w; col++ {
			img.Y[row*img.YStride+col] = raw[row*w+col]
		}
	}
	yOff := w * h
	for row := 0; row < h/2; row++ {
		for col := 0; col < w/2; col++ {
			img.Cb[row*img.CStride+col] = raw[yOff+row*(w/2)+col]
			img.Cr[row*img.CStride+col] = raw[yOff+(w/2)*(h/2)+row*(w/2)+col]
		}
	}
	return img
}

// Test6daysPFrameWithFFmpegRef decodes 6days P-frame using a perfect ffmpeg I-frame reference.
// This isolates whether the P-frame decode error is in the reference or the P-frame logic.
func Test6daysPFrameWithFFmpegRef(t *testing.T) {
	const w, h = 576, 256
	avi := "testdata/movie4.avi"
	frames := videoFrames(t, avi, 5)
	if len(frames) < 2 {
		t.Skip("not enough frames")
	}
	refImg := loadYCbCr(t, "/tmp/movie4_frame0_check.yuv", w, h)
	oracle, err := os.ReadFile("/tmp/movie4_frame1.yuv")
	if err != nil {
		t.Skipf("no oracle: %v", err)
	}
	var pFrame []byte
	for _, fr := range frames {
		if FrameType(fr) == picInter {
			pFrame = fr
			break
		}
	}
	if pFrame == nil {
		t.Skip("no P-frame")
	}
	pImg, err := DecodePFrame(pFrame, refImg, w, h)
	if err != nil {
		t.Fatalf("P-frame decode: %v", err)
	}
	ymse, ymaxDiff := planeMSE(pImg.Y, pImg.YStride, oracle, w, h)
	cbmse, _ := planeMSE(pImg.Cb, pImg.CStride, oracle[w*h:], w/2, h/2)
	crmse, _ := planeMSE(pImg.Cr, pImg.CStride, oracle[w*h+(w/2)*(h/2):], w/2, h/2)
	psnrY := 10 * math.Log10(255*255/ymse)
	if ymse == 0 {
		psnrY = 999
	}
	t.Logf("With ffmpeg I-ref: Y MSE=%.2f PSNR=%.1fdB maxDiff=%d Cb=%.2f Cr=%.2f", ymse, psnrY, ymaxDiff, cbmse, crmse)
	// Print MB(21,0) all 16 rows.
	for mbx, mby := 21, 0; mbx == 21; mbx++ {
		t.Logf("MB(%d,%d) all rows:", mbx, mby)
		for r := 0; r < 16; r++ {
			var got, want []int
			for c := 0; c < 16; c++ {
				py, px := mby*16+r, mbx*16+c
				got = append(got, int(pImg.Y[py*pImg.YStride+px]))
				want = append(want, int(oracle[py*w+px]))
			}
			t.Logf("  row%02d got:  %v", r, got)
			t.Logf("  row%02d want: %v", r, want)
		}
	}

	// Print all MBs in row 0 with high error.
	t.Log("High-error MBs in row 0:")
	for mbx := 0; mbx < w/16; mbx++ {
		var mse float64
		for r := 0; r < 16; r++ {
			for c := 0; c < 16; c++ {
				py, px := r, mbx*16+c
				d := int(pImg.Y[py*pImg.YStride+px]) - int(oracle[py*w+px])
				mse += float64(d * d)
			}
		}
		mse /= 256
		if mse > 50 {
			t.Logf("  MB(%d,0): MSE=%.0f", mbx, mse)
		}
	}
}

func TestPFramePixelCmp(t *testing.T) {
	cases := []struct {
		name string
		avi  string
		yuv1 string
		w, h int
	}{
		{"movie1", "testdata/movie1.avi", "/tmp/movie1_frame1.yuv", 512, 288},
		{"movie2", "testdata/movie2.avi", "/tmp/movie2_frame1.yuv", 576, 240},
		{"movie3", "testdata/movie3.avi", "/tmp/movie3_frame1.yuv", 512, 354},
		{"movie4", "testdata/movie4.avi", "/tmp/movie4_frame1.yuv", 576, 256},
		{"movie5", "testdata/movie5.avi", "/tmp/movie5_frame1.yuv", 512, 384},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			frames := videoFrames(t, tc.avi, 10)
			if len(frames) < 2 {
				t.Skip("fewer than 2 frames")
			}
			ref, err := os.ReadFile(tc.yuv1)
			if err != nil {
				t.Skipf("no oracle yuv: %v", err)
			}
			// Find first I-frame.
			var refImg *image.YCbCr
			var pFrames [][]byte
			foundI := false
			for _, fr := range frames {
				if !foundI && FrameType(fr) == picIntra {
					refImg, err = DecodeIntraFrame(fr, tc.w, tc.h)
					if err != nil {
						t.Fatalf("I-frame decode: %v", err)
					}
					foundI = true
					continue
				}
				if foundI && FrameType(fr) == picInter {
					pFrames = append(pFrames, fr)
					if len(pFrames) == 1 {
						break
					}
				}
			}
			if len(pFrames) == 0 {
				t.Skip("no P-frame found after I-frame")
			}
			pImg, err := DecodePFrame(pFrames[0], refImg, tc.w, tc.h)
			if err != nil {
				t.Fatalf("P-frame decode: %v", err)
			}
			w, h := tc.w, tc.h
			ySize := w * h
			ymse, ymaxDiff := planeMSE(pImg.Y, pImg.YStride, ref, w, h)
			cbmse, _ := planeMSE(pImg.Cb, pImg.CStride, ref[ySize:], w/2, h/2)
			crmse, _ := planeMSE(pImg.Cr, pImg.CStride, ref[ySize+w/2*h/2:], w/2, h/2)
			psnrY := 10 * math.Log10(255*255/ymse)
			if ymse == 0 {
				psnrY = 999
			}
			t.Logf("Y  MSE=%.2f PSNR=%.1fdB maxDiff=%d", ymse, psnrY, ymaxDiff)
			t.Logf("Cb MSE=%.2f", cbmse)
			t.Logf("Cr MSE=%.2f", crmse)
		})
	}
}
