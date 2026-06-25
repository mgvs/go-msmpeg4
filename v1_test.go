package msmpeg4

import (
	"encoding/base64"
	"encoding/binary"
	"os"
	"os/exec"
	"testing"
)

// Embedded MS-MPEG4 v1 (MPG4) I-frame bitstreams (80x64), produced by the original Microsoft codec
// mpg4c32.dll (see re/v1/NOTES.md). The test decodes each with ffmpeg (msmpeg4v1) as the pixel
// reference and checks DecodeIntraFrameV1 is bit-exact.
var v1Fixtures = map[string]string{
	"smooth": "AAABAABA/qJcOsmw7zErB1kzB0Pz0mHAnw5z0jByJ2Dgf2KfDoSYdwJ2DoSMHQ/OTYcdLhzlpmDliVg4H9irw7UXYdyK2DtJcwdD5UsOsmh21K2dZM2dD+XTQ5E8OXpGzgTtnA/bTw6qSHdKds6qRs6H8umhyxLDjpmzjpWzgftq4dpLod2K2ztRc2dD70rZ1kzZ2xLDrpodD+XSNnInbOXpIcCeHA/bTtnVSNndCeHQkh0P5dM2csStnHTQ46WHA/bVtnaS5s7sVw7UXQ6H0JWDrJmDvMS4ddNh0PzkjBwJ2DmBNhwJ8OB/ZJ2DqpGDuBPh0JMOh+cmYOOlYOcxNhx0uHA/sVbB2pIwdyK8O1F2HQ//8A==",
	"grad":   "AAABAABA/498Bu74Db/gN2/AaHp+A3b8Bt/wG7vgND0fAbu+A2/4DdvwGh6fgN2/Abf8Bu74DQ9HwG7vgNv+A3b8Bofw/wG7vgNv+A3d8Boej4Dd3wG3fAbu+A0PR8Bu74Db/gN3fAaHo+A3b8Bt/wG7vgND0fAbu+A2/4Dd3wGh8PwG7fgNv+A3d8Boej4Dd3wG3/Abt+A0PR8Bu74Db/gN3fAaHo+A3d8Bt/wG+VBgCQGANADwYDbUJQYMPAOofwGB72fRst+xzCwuLhBYbT9bpZWetJMHCXA/qdgEQyH9MXJh+EMfqhIEhV8vH5f8cKlTSPajq95nveyX7bf7jTDWa23uezdd8YBgCYGASR8PI0JIMBvlglj++abwIAMIFDpmNjkGC+f+bUCP9RGvfasBgQ2dStXBL9WFbvCghAwA4AcDAESTUgHkw4HI4qVP/a2Xj/7XiyfVt6BP8YYjSRr7bOqlTQ5+fw/AaH7fwG7fgNv+A3d8Boej4Dd3wG3/Abu+A0PR8BrlyYfhDH6oSBIVfLx+X/HCpU0j2o6veZ73sl+23+40w1mtt7ns3XeZBgCQGANADwYDbUJQYMPAOofwGB72fRst+xzCwuLhBYbT9bpZWetJMHCXA/qdgEQz8EBCBgBwA4GAIkmpAPJhwORxUqf+1svH/2vFk+rb0Cf4wxGkjX22dVKmhz8+H90DAEwMAkj4eRoSQYDfLBLH9803gQAYQKHTMbHIMF8/82oEf6iNe+1YDAhs6lauCX6sK3fMPgNv+A3d8Boej4Dd3wG3fAbu+A0P//A=",
}

func TestV1IntraFrame(t *testing.T) {
	const W, H = 80, 64
	for name, b64 := range v1Fixtures {
		bs, _ := base64.StdEncoding.DecodeString(b64)
		dir := t.TempDir()
		af := dir + "/v.avi"
		os.WriteFile(af, buildV1AVI(bs, W, H), 0o644)
		out, err := exec.Command("ffmpeg", "-v", "error", "-i", af, "-f", "rawvideo", "-pix_fmt", "yuv420p", "-").Output()
		if err != nil || len(out) < W*H {
			t.Skip("ffmpeg cannot decode msmpeg4v1")
		}
		img, err := DecodeIntraFrameV1(bs, W, H)
		if err != nil {
			t.Errorf("%s: %v", name, err)
			continue
		}
		var got []byte
		for y := 0; y < H; y++ {
			got = append(got, img.Y[y*img.YStride:y*img.YStride+W]...)
		}
		p := psnr(got, out[:W*H])
		t.Logf("v1 I-frame %s: luma=%.1f dB", name, p)
		if p < 90 {
			t.Errorf("%s: v1 I-frame not bit-exact (luma=%.1f)", name, p)
		}
	}
}

// buildV1AVI wraps a single MPG4 keyframe payload in a minimal AVI for ffmpeg.
func buildV1AVI(frame []byte, w, h int) []byte {
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
	bih := append(le(40), le(uint32(w))...)
	bih = append(bih, le(uint32(h))...)
	bih = append(bih, 1, 0, 24, 0)
	bih = append(bih, []byte("MPG4")...)
	bih = append(bih, make([]byte, 20)...)

	strhBody := append([]byte("vidsMPG4"), make([]byte, 12)...)
	strhBody = append(strhBody, le(1)...) // scale
	strhBody = append(strhBody, le(1)...) // rate
	strhBody = append(strhBody, make([]byte, 8)...)
	strhBody = append(strhBody, le(0xFFFFFFFF)...) // quality
	strhBody = append(strhBody, make([]byte, 8)...)
	strhBody = append(strhBody, le(uint32(w))...)
	strhBody = append(strhBody, le(uint32(h))...)

	strl := ck("LIST", append([]byte("strl"), append(ck("strh", strhBody), ck("strf", bih)...)...))
	avih := append(make([]byte, 12), le(0x10)...)
	avih = append(avih, le(1)...)
	avih = append(avih, make([]byte, 24)...)
	hdrl := ck("LIST", append([]byte("hdrl"), append(ck("avih", avih), strl...)...))
	movi := ck("LIST", append([]byte("movi"), ck("00dc", frame)...))
	body := append([]byte("AVI "), append(hdrl, movi...)...)
	return append([]byte("RIFF"), append(le(uint32(len(body))), body...)...)
}
