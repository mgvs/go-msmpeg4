package msmpeg4

import (
	"bytes"
	"image"
)

// Open sniffs an in-memory container (AVI or ASF/WMV) and returns a Demuxer for its video stream.
func Open(data []byte) (Demuxer, error) {
	switch {
	case len(data) >= 12 && string(data[0:4]) == "RIFF" && string(data[8:12]) == "AVI ":
		return OpenAVI(data)
	case len(data) >= 16 && bytes.Equal(data[0:16], asfHeaderObjGUID):
		return OpenASF(data)
	}
	return nil, errBadContainer
}

// DecodeAll demuxes a container and decodes every video frame through a stateful Decoder, returning
// the decoded images in order. A convenience wrapper over Open + NewDecoder + DecodeFrame.
func DecodeAll(data []byte) ([]*image.YCbCr, error) {
	dm, err := Open(data)
	if err != nil {
		return nil, err
	}
	fourcc, w, h, extradata := dm.Codec()
	dec, err := NewDecoder(fourcc, w, h, extradata)
	if err != nil {
		return nil, err
	}
	var out []*image.YCbCr
	for {
		pkt, err := dm.ReadPacket()
		if err != nil {
			break
		}
		img, err := dec.DecodeFrame(pkt)
		if err != nil {
			return out, err
		}
		out = append(out, img)
	}
	return out, nil
}
