package msmpeg4

import (
	"bytes"
	"encoding/binary"
	"io"
	"sort"
)

// ASF/WMV object GUIDs (Data1-3 little-endian, as on disk).
var (
	asfHeaderObjGUID = []byte{0x30, 0x26, 0xB2, 0x75, 0x8E, 0x66, 0xCF, 0x11, 0xA6, 0xD9, 0x00, 0xAA, 0x00, 0x62, 0xCE, 0x6C}
	asfDataObjGUID   = []byte{0x36, 0x26, 0xB2, 0x75, 0x8E, 0x66, 0xCF, 0x11, 0xA6, 0xD9, 0x00, 0xAA, 0x00, 0x62, 0xCE, 0x6C}
	asfFilePropGUID  = []byte{0xA1, 0xDC, 0xAB, 0x8C, 0x47, 0xA9, 0xCF, 0x11, 0x8E, 0xE4, 0x00, 0xC0, 0x0C, 0x20, 0x53, 0x65}
	asfStreamObjGUID = []byte{0x91, 0x07, 0xDC, 0xB7, 0xB7, 0xA9, 0xCF, 0x11, 0x8E, 0xE6, 0x00, 0xC0, 0x0C, 0x20, 0x53, 0x65}
	asfVideoTypeGUID = []byte{0xC0, 0xEF, 0x19, 0xBC, 0x4D, 0x5B, 0xCF, 0x11, 0xA8, 0xFD, 0x00, 0x80, 0x5F, 0x5C, 0x44, 0x2B}
)

// ASFDemuxer walks the data packets of an in-memory ASF/.wmv file, reassembling and yielding the
// video stream's media objects (frames) in order.
type ASFDemuxer struct {
	fourcc    string
	w, h      int
	extradata []byte
	packets   []byte
	pktSize   int
	streamNum int
	base      int

	pending map[int]*asfObj // in-progress media objects keyed by media-object number
	ready   [][]byte        // completed frames awaiting ReadPacket
}

type asfObj struct {
	frags    []asfFrag
	size     int
	haveSize bool
}

type asfFrag struct {
	offset int
	data   []byte
}

// OpenASF parses an in-memory ASF/.wmv file and prepares to read video packets.
func OpenASF(data []byte) (*ASFDemuxer, error) {
	pktSize, streamNum, dataOff, extradata, fourcc, w, h, ok := asfLayout(data)
	if !ok {
		return nil, errBadContainer
	}
	return &ASFDemuxer{
		fourcc:    fourcc,
		w:         w,
		h:         h,
		extradata: extradata,
		packets:   data[dataOff:],
		pktSize:   pktSize,
		streamNum: streamNum,
		pending:   map[int]*asfObj{},
	}, nil
}

func (d *ASFDemuxer) Codec() (string, int, int, []byte) {
	return d.fourcc, d.w, d.h, d.extradata
}

// ReadPacket reassembles and returns the next complete video media object (frame), or io.EOF.
func (d *ASFDemuxer) ReadPacket() ([]byte, error) {
	for len(d.ready) == 0 {
		if d.base+d.pktSize > len(d.packets) {
			return nil, io.EOF
		}
		asfParsePacket(d.packets[d.base:d.base+d.pktSize], func(sn, keyframe, mObj, offset, mediaObjSize int, payload []byte) {
			if sn != d.streamNum {
				return
			}
			o := d.pending[mObj]
			if o == nil {
				o = &asfObj{}
				d.pending[mObj] = o
			}
			o.frags = append(o.frags, asfFrag{offset, append([]byte(nil), payload...)})
			if mediaObjSize > 0 {
				o.size, o.haveSize = mediaObjSize, true
			}
			total := 0
			for _, f := range o.frags {
				total += len(f.data)
			}
			if o.haveSize && total >= o.size {
				d.ready = append(d.ready, assembleObj(o))
				delete(d.pending, mObj)
			}
		})
		d.base += d.pktSize
	}
	frame := d.ready[0]
	d.ready = d.ready[1:]
	return frame, nil
}

func assembleObj(o *asfObj) []byte {
	sort.SliceStable(o.frags, func(i, j int) bool { return o.frags[i].offset < o.frags[j].offset })
	var buf bytes.Buffer
	for _, f := range o.frags {
		buf.Write(f.data)
	}
	out := buf.Bytes()
	if o.haveSize && o.size > 0 && o.size <= len(out) {
		out = out[:o.size]
	}
	return out
}

// asfLayout parses the ASF header: data-packet size, video stream number, first-packet offset,
// codec extradata, plus the video FourCC and frame size from the stream's BITMAPINFOHEADER.
func asfLayout(data []byte) (pktSize, streamNum, dataOff int, extradata []byte, fourcc string, w, h int, ok bool) {
	if len(data) < 30 || !bytes.Equal(data[0:16], asfHeaderObjGUID) {
		return
	}
	hdrSize := int(binary.LittleEndian.Uint64(data[16:24]))
	if hdrSize < 30 || hdrSize > len(data) {
		return
	}
	body := data[30:hdrSize]
	for off := 0; off+24 <= len(body); {
		guid := body[off : off+16]
		objSize := int(binary.LittleEndian.Uint64(body[off+16 : off+24]))
		if objSize < 24 || off+objSize > len(body) {
			break
		}
		obj := body[off+24 : off+objSize]
		switch {
		case bytes.Equal(guid, asfFilePropGUID):
			if len(obj) >= 72 {
				pktSize = int(binary.LittleEndian.Uint32(obj[68:72])) // Min Data Packet Size
			}
		case bytes.Equal(guid, asfStreamObjGUID):
			if len(obj) >= 50 && bytes.Equal(obj[0:16], asfVideoTypeGUID) {
				streamNum = int(binary.LittleEndian.Uint16(obj[48:50]) & 0x7F)
				extradata, fourcc, w, h = asfVideoInfo(obj)
			}
		}
		off += objSize
	}
	// Data Object follows: GUID(16)+Size(8)+FileID(16)+TotalPackets(8)+Reserved(2) = 50 bytes header.
	if pktSize <= 0 || streamNum == 0 || hdrSize+50 > len(data) {
		return
	}
	if !bytes.Equal(data[hdrSize:hdrSize+16], asfDataObjGUID) {
		return
	}
	return pktSize, streamNum, hdrSize + 50, extradata, fourcc, w, h, true
}

// asfVideoInfo extracts extradata, FourCC and dimensions from a video Stream Properties Object's
// type-specific data (Width, Height, then a BITMAPINFOHEADER + extradata).
func asfVideoInfo(obj []byte) (extradata []byte, fourcc string, w, h int) {
	// obj: StreamType(16)+ErrorCorr(16)+TimeOffset(8)+TSDataLen(4)+ECDataLen(4)+Flags(2)+Reserved(4)+TS…
	if len(obj) < 54 {
		return
	}
	tsLen := int(binary.LittleEndian.Uint32(obj[40:44]))
	ts := obj[54:]
	if tsLen < len(ts) {
		ts = ts[:tsLen]
	}
	// Video type-specific: Width(4) Height(4) Flags(1) FormatDataSize(2) [11] + BITMAPINFOHEADER + extra.
	if len(ts) < 15+20 {
		return
	}
	w = int(int32(binary.LittleEndian.Uint32(ts[0:4])))
	h = int(int32(binary.LittleEndian.Uint32(ts[4:8])))
	if h < 0 {
		h = -h
	}
	bih := ts[11:] // BITMAPINFOHEADER
	biSize := int(binary.LittleEndian.Uint32(bih[0:4]))
	if biSize >= 20 {
		fourcc = string(bih[16:20]) // biCompression
	}
	if biSize > 40 && 40 <= len(bih) && biSize <= len(bih) {
		extradata = append([]byte(nil), bih[40:biSize]...)
	}
	return
}

// asfVar reads a variable-length integer whose width is given by a 2-bit length type
// (0→0 bytes/value 0, 1→1, 2→2, 3→4), advancing *pos.
func asfVar(p []byte, pos *int, lenType int) int {
	var n int
	switch lenType {
	case 1:
		if *pos < len(p) {
			n = int(p[*pos])
			*pos++
		}
	case 2:
		if *pos+2 <= len(p) {
			n = int(binary.LittleEndian.Uint16(p[*pos:]))
			*pos += 2
		}
	case 3:
		if *pos+4 <= len(p) {
			n = int(binary.LittleEndian.Uint32(p[*pos:]))
			*pos += 4
		}
	}
	return n
}

// asfParsePacket parses one fixed-size data packet and calls emit for each payload.
func asfParsePacket(p []byte, emit func(sn, keyframe, mObj, offset, mediaObjSize int, payload []byte)) {
	if len(p) == 0 {
		return
	}
	pos := 0
	if p[0]&0x80 != 0 { // Error Correction Data present
		ecLen := int(p[0] & 0x0F)
		pos = 1 + ecLen
	}
	if pos >= len(p) {
		return
	}
	lenTypeFlags := p[pos]
	pos++
	if pos >= len(p) {
		return
	}
	propFlags := p[pos]
	pos++

	multiple := lenTypeFlags&0x01 != 0
	seqType := int(lenTypeFlags>>1) & 0x03
	padType := int(lenTypeFlags>>3) & 0x03
	pktLenType := int(lenTypeFlags>>5) & 0x03

	replLenType := int(propFlags) & 0x03
	offType := int(propFlags>>2) & 0x03
	mObjType := int(propFlags>>4) & 0x03

	_ = asfVar(p, &pos, pktLenType)
	_ = asfVar(p, &pos, seqType)
	padding := asfVar(p, &pos, padType)
	pos += 6 // send time (4) + duration (2)
	if pos > len(p) {
		return
	}
	dataEnd := len(p) - padding
	if dataEnd > len(p) || dataEnd < 0 {
		dataEnd = len(p)
	}

	readOne := func(payLenType int, multi bool) bool {
		if pos+1 > dataEnd {
			return false
		}
		sn := p[pos]
		pos++
		keyframe := int(sn & 0x80)
		stream := int(sn & 0x7F)
		mObj := asfVar(p, &pos, mObjType)
		offset := asfVar(p, &pos, offType)
		replLen := asfVar(p, &pos, replLenType)
		mediaObjSize := 0
		if replLen >= 8 && pos+8 <= dataEnd {
			mediaObjSize = int(binary.LittleEndian.Uint32(p[pos:]))
		}
		if pos+replLen > dataEnd {
			return false
		}
		pos += replLen
		var payLen int
		if multi {
			payLen = asfVar(p, &pos, payLenType)
		} else {
			payLen = dataEnd - pos
		}
		if payLen < 0 || pos+payLen > dataEnd {
			return false
		}
		emit(stream, keyframe, mObj, offset, mediaObjSize, p[pos:pos+payLen])
		pos += payLen
		return true
	}

	if !multiple {
		readOne(0, false)
		return
	}
	if pos >= dataEnd {
		return
	}
	payloadFlags := p[pos]
	pos++
	count := int(payloadFlags & 0x3F)
	payLenType := int(payloadFlags>>6) & 0x03
	for i := 0; i < count; i++ {
		if !readOne(payLenType, true) {
			return
		}
	}
}
