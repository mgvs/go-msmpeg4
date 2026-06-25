package msmpeg4

import (
	"errors"
	"image"
	"strings"
)

// Version identifies a member of the Microsoft MPEG-4 / Windows Media Video family.
type Version int

const (
	VersionV1   Version = iota // MP41 / DIV1 (I-frames bit-exact; P-frames bit-exact on integer motion)
	VersionV2                  // MP42 / DIV2
	VersionV3                  // DIV3 / MP43 and relatives
	VersionWMV1                // WMV1 (Windows Media Video 7)
	VersionWMV2                // WMV2 (Windows Media Video 8)
)

var errNoReference = errors.New("msmpeg4: P-frame decoded before any I-frame (no reference)")
var errUnknownCodec = errors.New("msmpeg4: unknown FourCC")

// VersionFromFourCC maps a container FourCC (case-insensitive) to a Version.
func VersionFromFourCC(fourcc string) (Version, error) {
	switch strings.ToUpper(strings.TrimSpace(fourcc)) {
	case "MP41", "DIV1", "MPG4":
		return VersionV1, nil
	case "MP42", "DIV2":
		return VersionV2, nil
	case "DIV3", "MP43", "DIV4", "DIV5", "DIV6", "AP41", "COL1", "COL0", "MPG3", "DVX3", "3IV1", "3IVD":
		return VersionV3, nil
	case "WMV1", "WMV7":
		return VersionWMV1, nil
	case "WMV2", "WMV8":
		return VersionWMV2, nil
	}
	return 0, errUnknownCodec
}

// Decoder decodes a whole MS-MPEG4/WMV video stream frame by frame, automatically dispatching
// I/P frames and maintaining the reference picture. Construct it with NewDecoder, then feed coded
// frames to DecodeFrame in stream order.
//
// WMV2 no_rounding is tracked across frames (it toggles each P-frame), so long P runs stay
// bit-exact on half-pel motion compensation.
type Decoder struct {
	version   Version
	w, h      int
	extradata []byte
	ref       *image.YCbCr
	bitRate   int  // WMV1: parsed from the I-frame ext-header, needed by its P-frames
	noRound   bool // WMV2: no_rounding state, toggled each P-frame (1 after I)
}

// NewDecoder creates a stream decoder for the given FourCC, frame size and codec extradata
// (the 4-byte WMV feature flags for WMV1/WMV2; may be nil for the others).
func NewDecoder(fourcc string, w, h int, extradata []byte) (*Decoder, error) {
	v, err := VersionFromFourCC(fourcc)
	if err != nil {
		return nil, err
	}
	return &Decoder{version: v, w: w, h: h, extradata: extradata}, nil
}

// NewDecoderVersion is like NewDecoder but takes an explicit Version.
func NewDecoderVersion(v Version, w, h int, extradata []byte) (*Decoder, error) {
	return &Decoder{version: v, w: w, h: h, extradata: extradata}, nil
}

// Version reports the codec version this decoder handles.
func (d *Decoder) Version() Version { return d.version }

// peekIntra reads the picture coding type without consuming the packet: WMV2 uses 1 bit, v1 puts
// it after a 32-bit start code + 5-bit frame number, the others read 2 bits up front; 0 = I-frame.
func (d *Decoder) peekIntra(pkt []byte) bool {
	r := newBitReader(pkt)
	switch d.version {
	case VersionWMV2:
		return r.bit() == 0
	case VersionV1:
		r.u(32) // start code
		r.u(5)  // frame number
		return r.u(2) == 0
	}
	return r.u(2) == 0
}

// DecodeFrame decodes one coded frame (auto-detecting I vs P), updates the reference picture and
// returns the decoded image. P-frames before the first I-frame return errNoReference.
func (d *Decoder) DecodeFrame(pkt []byte) (*image.YCbCr, error) {
	intra := d.peekIntra(pkt)
	if !intra && d.ref == nil {
		return nil, errNoReference
	}
	var img *image.YCbCr
	var err error
	if intra {
		img, err = d.decodeIntra(pkt)
	} else {
		img, err = d.decodeInter(pkt)
	}
	if err != nil {
		return nil, err
	}
	d.ref = img
	return img, nil
}

func (d *Decoder) decodeIntra(pkt []byte) (*image.YCbCr, error) {
	switch d.version {
	case VersionV1:
		return DecodeIntraFrameV1(pkt, d.w, d.h)
	case VersionV2:
		return DecodeIntraFrameV2(pkt, d.w, d.h)
	case VersionV3:
		return DecodeIntraFrame(pkt, d.w, d.h)
	case VersionWMV1:
		d.bitRate = parseWMV1BitRate(pkt)
		return DecodeIntraFrameWMV1(pkt, d.w, d.h)
	case VersionWMV2:
		img, err := DecodeIntraFrameWMV2(pkt, d.w, d.h, d.extradata)
		d.noRound = true // no_rounding = 1 after an I-frame; the first P toggles it to 0
		return img, err
	}
	return nil, errUnknownCodec
}

func (d *Decoder) decodeInter(pkt []byte) (*image.YCbCr, error) {
	switch d.version {
	case VersionV1:
		return DecodePFrameV1(pkt, d.ref, d.w, d.h)
	case VersionV2:
		return DecodePFrameV2(pkt, d.ref, d.w, d.h)
	case VersionV3:
		return DecodePFrame(pkt, d.ref, d.w, d.h)
	case VersionWMV1:
		return DecodePFrameWMV1(pkt, d.ref, d.w, d.h, d.bitRate)
	case VersionWMV2:
		d.noRound = !d.noRound // flipflop_rounding: no_rounding ^= 1 each P-frame
		return DecodePFrameWMV2(pkt, d.ref, d.w, d.h, d.extradata, d.noRound)
	}
	return nil, errUnknownCodec
}

// parseWMV1BitRate extracts bit_rate from a WMV1 I-frame ext-header: pictype(2) q(5) slice(5)
// fps(5) bit_rate(11).
func parseWMV1BitRate(pkt []byte) int {
	r := newBitReader(pkt)
	r.u(2)
	r.u(5)
	r.u(5)
	r.u(5)
	return r.u(11) * 1024
}
