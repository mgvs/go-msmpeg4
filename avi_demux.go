package msmpeg4

import (
	"encoding/binary"
	"errors"
	"io"
)

// Demuxer yields coded video packets from a container together with the stream's codec info.
// Feed Codec() into NewDecoder and ReadPacket() results into Decoder.DecodeFrame.
type Demuxer interface {
	// Codec reports the video stream's FourCC, frame size and codec extradata.
	Codec() (fourcc string, w, h int, extradata []byte)
	// ReadPacket returns the next coded video frame, or io.EOF when the stream ends.
	ReadPacket() ([]byte, error)
}

var errBadContainer = errors.New("msmpeg4: malformed container")

// AVIDemuxer walks the movi LIST of an in-memory AVI file, yielding video-stream packets.
type AVIDemuxer struct {
	fourcc       string
	w, h         int
	extradata    []byte
	data         []byte
	pos, moviEnd int
}

// OpenAVI parses an in-memory AVI (RIFF/AVI ) and prepares to read video packets.
func OpenAVI(data []byte) (*AVIDemuxer, error) {
	if len(data) < 12 || string(data[0:4]) != "RIFF" || string(data[8:12]) != "AVI " {
		return nil, errBadContainer
	}
	fcc := aviCodecFourCC(data)
	w, h := aviDims(data)
	start, end := aviMoviRange(data)
	if start < 0 {
		return nil, errBadContainer
	}
	d := &AVIDemuxer{
		fourcc:    string(fcc[:]),
		w:         w,
		h:         h,
		extradata: aviExtradata(data),
		data:      data,
		pos:       start,
		moviEnd:   end,
	}
	return d, nil
}

func (d *AVIDemuxer) Codec() (string, int, int, []byte) {
	return d.fourcc, d.w, d.h, d.extradata
}

// ReadPacket returns the next 00dc/00db video chunk payload. Non-video chunks (audio, etc.) and
// "rec " grouping LISTs are skipped/descended transparently.
func (d *AVIDemuxer) ReadPacket() ([]byte, error) {
	data := d.data
	for d.pos+8 <= d.moviEnd {
		id := data[d.pos : d.pos+4]
		sz := int(binary.LittleEndian.Uint32(data[d.pos+4 : d.pos+8]))
		if sz < 0 {
			return nil, errBadContainer
		}
		body := d.pos + 8
		// Descend into "rec " grouping lists rather than treating them as a chunk.
		if string(id) == "LIST" && body+4 <= len(data) && string(data[body:body+4]) == "rec " {
			d.pos = body + 4
			continue
		}
		end := body + sz
		if end > len(data) {
			end = len(data)
		}
		d.pos = body + sz + (sz & 1)
		if d.pos <= body-8 { // overflow guard
			return nil, errBadContainer
		}
		if sz > 0 && aviIsVideoCk(id) {
			return data[body:end], nil
		}
	}
	return nil, io.EOF
}

// aviExtradata returns the bytes trailing the BITMAPINFOHEADER in the video strf (codec config /
// WMV feature flags), or nil if none.
func aviExtradata(data []byte) []byte {
	i := 12
	for i+8 <= len(data) {
		id := string(data[i : i+4])
		sz := int(binary.LittleEndian.Uint32(data[i+4 : i+8]))
		if sz < 0 {
			break
		}
		body := i + 8
		if id == "LIST" && body+4 <= len(data) {
			switch string(data[body : body+4]) {
			case "hdrl":
				if ex := scanHdrlExtradata(data, body+4, body+sz); ex != nil {
					return ex
				}
			case "strl":
				if ex := scanStrlExtradata(data, body+4, body+sz); ex != nil {
					return ex
				}
			case "movi":
				return nil
			}
		}
		next := i + 8 + sz + (sz & 1)
		if next <= i {
			break
		}
		i = next
	}
	return nil
}

func scanHdrlExtradata(data []byte, off, end int) []byte {
	if end > len(data) {
		end = len(data)
	}
	for off+8 <= end {
		id := string(data[off : off+4])
		sz := int(binary.LittleEndian.Uint32(data[off+4 : off+8]))
		if sz < 0 {
			break
		}
		body := off + 8
		if id == "LIST" && body+4 <= end && string(data[body:body+4]) == "strl" {
			if ex := scanStrlExtradata(data, body+4, body+sz); ex != nil {
				return ex
			}
		}
		off += 8 + sz + (sz & 1)
	}
	return nil
}

func scanStrlExtradata(data []byte, off, end int) []byte {
	if end > len(data) {
		end = len(data)
	}
	for off+8 <= end {
		id := string(data[off : off+4])
		sz := int(binary.LittleEndian.Uint32(data[off+4 : off+8]))
		if sz < 0 {
			break
		}
		body := off + 8
		// strf = BITMAPINFOHEADER (40 bytes) + optional extradata.
		if id == "strf" && sz > 40 && body+sz <= len(data) {
			ex := make([]byte, sz-40)
			copy(ex, data[body+40:body+sz])
			return ex
		}
		off += 8 + sz + (sz & 1)
	}
	return nil
}
