package msmpeg4

import (
	"encoding/base64"
	"encoding/binary"
	"os"
	"os/exec"
	"testing"
)

// TestV1PFrame checks v1 (MP41) P-frame decoding is bit-exact vs ffmpeg on integer-motion content,
// using committed frame fixtures (an I-frame followed by P-frames). Skipped if ffmpeg is absent.
func TestV1PFrame(t *testing.T) {
	const w, h = 96, 64
	frames := make([][]byte, len(v1PFrames))
	for i, s := range v1PFrames {
		frames[i], _ = base64.StdEncoding.DecodeString(s)
	}
	dir := t.TempDir()
	af := dir + "/v.avi"
	os.WriteFile(af, buildV1MultiAVI(frames, w, h), 0o644)
	out, err := exec.Command("ffmpeg", "-v", "error", "-i", af, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
	fsz := w * h * 3 / 2
	if err != nil || len(out) < len(frames)*fsz {
		t.Skip("ffmpeg cannot decode msmpeg4v1")
	}
	cur, err := DecodeIntraFrameV1(frames[0], w, h)
	if err != nil {
		t.Fatalf("I-frame: %v", err)
	}
	for f := 1; f < len(frames); f++ {
		p, err := DecodePFrameV1(frames[f], cur, w, h)
		if err != nil {
			t.Fatalf("P%d: %v", f, err)
		}
		var g []byte
		for y := 0; y < h; y++ {
			g = append(g, p.Y[y*p.YStride:y*p.YStride+w]...)
		}
		ps := psnr(g, out[f*fsz:f*fsz+w*h])
		t.Logf("v1 P%d: luma=%.1f dB", f, ps)
		if ps < 90 {
			t.Errorf("v1 P%d not bit-exact: luma=%.1f", f, ps)
		}
		cur = p
	}
}

// buildV1MultiAVI wraps several MPG4 frame payloads (first a keyframe) into a minimal AVI, matching
// buildV1AVI's layout but with one 00dc chunk per frame.
func buildV1MultiAVI(frames [][]byte, w, h int) []byte {
	le := func(v uint32) []byte {
		b := make([]byte, 4)
		binary.LittleEndian.PutUint32(b, v)
		return b
	}
	ck := func(id string, d []byte) []byte {
		out := append([]byte(id), le(uint32(len(d)))...)
		out = append(out, d...)
		if len(d)&1 == 1 {
			out = append(out, 0)
		}
		return out
	}
	nf := uint32(len(frames))
	bih := append(le(40), le(uint32(w))...)
	bih = append(bih, le(uint32(h))...)
	bih = append(bih, 1, 0, 24, 0)
	bih = append(bih, []byte("MPG4")...)
	bih = append(bih, make([]byte, 20)...)

	strhBody := append([]byte("vidsMPG4"), make([]byte, 12)...)
	strhBody = append(strhBody, le(1)...) // scale
	strhBody = append(strhBody, le(1)...) // rate
	strhBody = append(strhBody, make([]byte, 4)...)
	strhBody = append(strhBody, le(nf)...) // length
	strhBody = append(strhBody, le(0xFFFFFFFF)...)
	strhBody = append(strhBody, make([]byte, 8)...)
	strhBody = append(strhBody, le(uint32(w))...)
	strhBody = append(strhBody, le(uint32(h))...)

	strl := ck("LIST", append([]byte("strl"), append(ck("strh", strhBody), ck("strf", bih)...)...))
	avih := append(make([]byte, 12), le(0x10)...)
	avih = append(avih, le(nf)...) // total frames
	avih = append(avih, make([]byte, 24)...)
	hdrl := ck("LIST", append([]byte("hdrl"), append(ck("avih", avih), strl...)...))
	movi := []byte("movi")
	for _, f := range frames {
		movi = append(movi, ck("00dc", f)...)
	}
	movi = ck("LIST", movi)
	body := append([]byte("AVI "), append(hdrl, movi...)...)
	return append([]byte("RIFF"), append(le(uint32(len(body))), body...)...)
}
