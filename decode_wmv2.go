package msmpeg4

import "image"

// DecodeIntraFrameWMV2 decodes one WMV2 (Windows Media Video 8) I-frame to an image.
//
// WMV2 shares WMV1's intra-MB coding (j_type=0 path): the same MCBPC/DC/RL/scan/DC-scale
// tables. Only the picture header differs, and the per-stream feature flags live in the 32-bit
// codec extradata: fps(5) bit_rate(11) mspel(1) loop(1) abt(1) j_type_bit(1) top_left_mv(1)
// per_mb_rl_bit(1) code(3). When extradata is absent the flags default to the common case
// (j_type_bit=1, per_mb_rl_bit=1, as produced by encoders). per_mb_rl_table=1 (per-MB RL index)
// is supported; J-frames (j_type=1), the in-loop deblocking filter, and P-frames are not.
func DecodeIntraFrameWMV2(data []byte, w, h int, extradata []byte) (*image.YCbCr, error) {
	jTypeBit, perMBRLBit, loopFilter := true, true, false
	if len(extradata) >= 4 {
		e := newBitReader(extradata)
		e.u(5)  // fps
		e.u(11) // bit_rate
		e.bit() // mspel
		loopFilter = e.bit() == 1
		e.bit() // abt_flag
		jTypeBit = e.bit() == 1
		e.bit() // top_left_mv_flag
		perMBRLBit = e.bit() == 1
	}

	r := newBitReader(data)
	if r.bit() != 0 { // picture coding type: 0 = I-frame
		return nil, errUnsupportedConfig
	}
	r.u(7) // "I7" code (informational, discarded)
	q := r.u(5)
	if q == 0 || q > 31 {
		return nil, errDecode
	}
	jType := 0
	if jTypeBit {
		jType = r.bit()
	}
	if jType != 0 {
		return nil, errUnsupportedConfig // J-frame coding not supported
	}
	perMBRL := 0
	if perMBRLBit {
		perMBRL = r.bit()
	}
	rcIdx, rtIdx := 0, 0
	if perMBRL == 0 {
		rcIdx = r.c3()
		rtIdx = r.c3()
	}
	dcIdx := r.bit()

	return wmv1IntraBody(r, q, rtIdx, rcIdx, dcIdx, w, h, perMBRL != 0, loopFilter)
}
