package msmpeg4

// Picture-level fields recovered by black-box bitstream RE (see re/NOTES.md).
// Confirmed on crafted single-MB frames (qscale sweep) and the 5 real samples.

const (
	picIntra = iota // I-frame
	picInter        // P-frame
)

// pictureHeader is the per-frame header. For v3 I-frames the layout is:
//
//	[2 bits picture-coding][5 bits quantizer] then the macroblock layer.
//
// The 2 leading bits are 00 on every observed I-frame; the P-frame encoding of
// this field is still to be reversed (only needed to classify I vs P on seek).
type pictureHeader struct {
	codingType int
	quantizer  int
}

// parsePictureHeader reads the picture header from a v3 frame.
func parsePictureHeader(r *bitReader) pictureHeader {
	h := pictureHeader{}
	lead := r.u(2)
	if lead == 0 {
		h.codingType = picIntra
	} else {
		h.codingType = picInter
	}
	h.quantizer = r.u(5)
	return h
}

// FrameType peeks at the first two bits to return picIntra or picInter
// without allocating a bitReader.
func FrameType(data []byte) int {
	if len(data) == 0 || data[0]>>6 == 0 {
		return picIntra
	}
	return picInter
}
