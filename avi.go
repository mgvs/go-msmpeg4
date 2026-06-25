package msmpeg4

import (
	"encoding/binary"
	"errors"
	"image"
	"io"
)

// aviIsV2FourCC reports whether the FourCC is an MS-MPEG4 v2 variant (MP42/DIV2).
func aviIsV2FourCC(fcc [4]byte) bool {
	s := string(fcc[:])
	switch s {
	case "MP42", "mp42", "DIV2", "div2", "MPG4", "mpg4":
		return true
	}
	return false
}

// aviCodecFourCC extracts the video codec FourCC. It prefers the strf
// BITMAPINFOHEADER biCompression, and falls back to the strh vids fccHandler
// when biCompression is zero (some muxers leave biCompression empty).
// The strl list is nested inside the hdrl list, so we descend into it.
// Returns [4]byte{} if not found.
func aviCodecFourCC(data []byte) (fcc [4]byte) {
	i := 12
	for i+8 <= len(data) {
		id := string(data[i : i+4])
		sz := int(binary.LittleEndian.Uint32(data[i+4 : i+8]))
		if sz < 0 {
			break
		}
		dataOff := i + 8
		if id == "LIST" && dataOff+4 <= len(data) {
			switch string(data[dataOff : dataOff+4]) {
			case "hdrl":
				if f, ok := scanHdrlFourCC(data, dataOff+4, dataOff+sz); ok {
					return f
				}
			case "strl": // some files place strl at the top level
				if f, ok := scanStrlFourCC(data, dataOff+4, dataOff+sz); ok {
					return f
				}
			case "movi":
				return fcc
			}
		}
		next := i + 8 + sz + (sz & 1)
		if next <= i {
			break
		}
		i = next
	}
	return fcc
}

// scanHdrlFourCC descends a hdrl LIST to find the video strl's codec FourCC.
func scanHdrlFourCC(data []byte, off, end int) ([4]byte, bool) {
	if end > len(data) {
		end = len(data)
	}
	for off+8 <= end {
		id := string(data[off : off+4])
		sz := int(binary.LittleEndian.Uint32(data[off+4 : off+8]))
		if sz < 0 {
			break
		}
		dataOff := off + 8
		if id == "LIST" && dataOff+4 <= end && string(data[dataOff:dataOff+4]) == "strl" {
			if f, ok := scanStrlFourCC(data, dataOff+4, dataOff+sz); ok {
				return f, true
			}
		}
		off += 8 + sz + (sz & 1)
	}
	return [4]byte{}, false
}

// scanStrlFourCC reads a single strl: strh fccHandler (for vids streams) and
// strf biCompression, returning biCompression if non-zero else the handler.
func scanStrlFourCC(data []byte, off, end int) ([4]byte, bool) {
	if end > len(data) {
		end = len(data)
	}
	var handler, comp [4]byte
	isVideo := false
	for off+8 <= end {
		cid := string(data[off : off+4])
		csz := int(binary.LittleEndian.Uint32(data[off+4 : off+8]))
		if csz < 0 {
			break
		}
		dataOff := off + 8
		switch {
		case cid == "strh" && dataOff+8 <= end:
			if string(data[dataOff:dataOff+4]) == "vids" {
				isVideo = true
				copy(handler[:], data[dataOff+4:dataOff+8]) // fccHandler
			}
		case cid == "strf" && csz >= 20 && dataOff+20 <= end:
			copy(comp[:], data[dataOff+16:dataOff+20]) // biCompression
		}
		off += 8 + csz + (csz & 1)
	}
	if !isVideo {
		return [4]byte{}, false
	}
	if comp != ([4]byte{}) {
		return comp, true
	}
	return handler, true
}

// DecodeAVIFirstFrame reads a keyframe near 10% into the video from an AVI
// containing MS-MPEG4 v1/v2/v3 and decodes it. The codec variant is auto-detected
// from the strf FourCC; MP42/DIV2 use the v2 path, all others use v3.
// Uses idx1 for a keyframe seek; falls back to a linear 512 KB scan.
func DecodeAVIFirstFrame(r io.ReadSeeker) (image.Image, error) {
	fileSize, err := r.Seek(0, io.SeekEnd)
	if err != nil {
		return nil, err
	}

	// Read enough of the header to parse dimensions and find the movi LIST.
	const hdrRead = 128 * 1024
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return nil, err
	}
	hdrBuf := make([]byte, hdrRead)
	hn, _ := io.ReadFull(r, hdrBuf)
	hdr := hdrBuf[:hn]

	if len(hdr) < 12 || string(hdr[:4]) != "RIFF" || string(hdr[8:12]) != "AVI " {
		return nil, errors.New("msmpeg4: not an AVI file")
	}
	w, h := aviDims(hdr)
	if w <= 0 || h <= 0 {
		return nil, errors.New("msmpeg4: cannot read AVI video dimensions")
	}

	isV2 := aviIsV2FourCC(aviCodecFourCC(hdr))
	decode := func(frame []byte) (image.Image, error) {
		if isV2 {
			return DecodeIntraFrameV2(frame, w, h)
		}
		return DecodeIntraFrame(frame, w, h)
	}

	moviStart, _ := aviMoviRange(hdr)

	if moviStart >= 0 {
		if frame, err := aviIdx1Keyframe(r, fileSize, int64(moviStart), 0.10); err == nil {
			if img, err := decode(frame); err == nil {
				return img, nil
			}
		}
		if frame, err := aviWalkMovi(r, fileSize, int64(moviStart), 0.10); err == nil {
			if img, err := decode(frame); err == nil {
				return img, nil
			}
		}
	}

	// Fall back to a linear scan of the first 512 KB.
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return nil, err
	}
	scanBuf := make([]byte, 512*1024)
	n, _ := io.ReadFull(r, scanBuf)
	data := scanBuf[:n]

	frame := aviFirstFrame(data)
	if frame == nil {
		return nil, errors.New("msmpeg4: no intra frame in first 512 KB")
	}
	return decode(frame)
}

// aviIdx1Keyframe finds a keyframe near `fraction` using the AVI idx1 index.
// moviDataStart is the file offset of the first byte after the "movi" type marker.
func aviIdx1Keyframe(r io.ReadSeeker, fileSize, moviDataStart int64, fraction float64) ([]byte, error) {
	// Read the tail of the file to find the idx1 chunk.
	const tailSize = 4 * 1024 * 1024
	tailFrom := fileSize - tailSize
	if tailFrom < 0 {
		tailFrom = 0
	}
	if _, err := r.Seek(tailFrom, io.SeekStart); err != nil {
		return nil, err
	}
	tail := make([]byte, tailSize)
	tn, _ := io.ReadFull(r, tail)
	tail = tail[:tn]

	// Locate the idx1 chunk.
	idx1Off := -1
	for i := 0; i+8 <= len(tail); i++ {
		if string(tail[i:i+4]) == "idx1" {
			idx1Off = i
			break
		}
	}
	if idx1Off < 0 {
		return nil, errors.New("msmpeg4: idx1 not found")
	}
	idx1Sz := int(binary.LittleEndian.Uint32(tail[idx1Off+4:]))
	idx1 := tail[idx1Off+8:]
	if idx1Sz < len(idx1) {
		idx1 = idx1[:idx1Sz]
	}
	n := len(idx1) / 16
	if n == 0 {
		return nil, errors.New("msmpeg4: idx1 empty")
	}

	// Resolve the movi-relative vs. absolute offset convention by anchoring on
	// the FIRST entry in idx1 (regardless of type) — same technique as go-mpeg4/riff.
	// The first chunk in movi (often audio) is at offset 0 in movi-relative files,
	// so base = moviDataStart - firstEntryOffset.
	if n == 0 {
		return nil, errors.New("msmpeg4: idx1 empty")
	}
	firstEntryOff := int64(binary.LittleEndian.Uint32(idx1[8:])) // offset field of entry 0
	base := moviDataStart - firstEntryOff

	// Collect keyframe file positions.
	type kf struct {
		fileOff int64
		size    int
	}
	var keys []kf
	for i := 0; i < n; i++ {
		e := idx1[i*16:]
		if !aviIsVideoCk(e[:4]) {
			continue
		}
		flags := binary.LittleEndian.Uint32(e[4:])
		if flags&0x10 == 0 { // AVIIF_KEYFRAME
			continue
		}
		off := int64(binary.LittleEndian.Uint32(e[8:]))
		sz := int(binary.LittleEndian.Uint32(e[12:]))
		if sz <= 0 || sz > 50*1024*1024 {
			continue
		}
		keys = append(keys, kf{base + off + 8, sz}) // +8: skip the 8-byte chunk header
	}
	if len(keys) == 0 {
		return nil, errors.New("msmpeg4: no video keyframes in idx1")
	}

	readAt := func(k kf) ([]byte, error) {
		if _, err := r.Seek(k.fileOff, io.SeekStart); err != nil {
			return nil, err
		}
		buf := make([]byte, k.size)
		if _, err := io.ReadFull(r, buf); err != nil {
			return nil, err
		}
		return buf, nil
	}

	// Validate the base convention by checking the first keyframe is an I-frame.
	first, err := readAt(keys[0])
	if err != nil || FrameType(first) != picIntra {
		return nil, errors.New("msmpeg4: idx1 base validation failed")
	}

	// Return the keyframe nearest to the target fraction.
	ti := int(float64(len(keys)) * fraction)
	if ti <= 0 {
		return first, nil
	}
	if ti >= len(keys) {
		ti = len(keys) - 1
	}
	frame, err := readAt(keys[ti])
	if err != nil || FrameType(frame) != picIntra {
		return first, nil // fall back to the validated first keyframe
	}
	return frame, nil
}

// aviWalkMovi walks movi chunk headers (reading only 8 bytes per chunk, seeking
// over data) to find the first video I-frame at or after `fraction` of the movi
// byte range. This is the fallback when idx1 is absent.
func aviWalkMovi(r io.ReadSeeker, fileSize, moviDataStart int64, fraction float64) ([]byte, error) {
	moviSize := fileSize - moviDataStart
	if moviSize <= 0 {
		return nil, errors.New("msmpeg4: empty movi")
	}
	target := moviDataStart + int64(float64(moviSize)*fraction)

	if _, err := r.Seek(moviDataStart, io.SeekStart); err != nil {
		return nil, err
	}
	hdr := make([]byte, 8)
	pos := moviDataStart
	for pos+8 <= fileSize {
		if _, err := r.Seek(pos, io.SeekStart); err != nil {
			break
		}
		if _, err := io.ReadFull(r, hdr); err != nil {
			break
		}
		sz := int64(binary.LittleEndian.Uint32(hdr[4:]))
		if sz < 0 || sz > 200*1024*1024 {
			break
		}
		if aviIsVideoCk(hdr[:4]) && sz > 0 && pos >= target {
			frame := make([]byte, sz)
			if _, err := io.ReadFull(r, frame); err != nil {
				break
			}
			if FrameType(frame) == picIntra {
				return frame, nil
			}
		}
		pos += 8 + sz + (sz & 1)
	}
	return nil, errors.New("msmpeg4: no I-frame found at target fraction")
}

// aviDims extracts video width/height from the AVI header.
// Prefers BITMAPINFOHEADER (strf) over the main AVI header (avih).
func aviDims(data []byte) (w, h int) {
	var avihW, avihH int
	i := 12 // skip "RIFF" + size + "AVI "
	for i+8 <= len(data) {
		id := string(data[i : i+4])
		sz := int(binary.LittleEndian.Uint32(data[i+4 : i+8]))
		if sz < 0 || sz > len(data) {
			break
		}
		dataOff := i + 8
		switch id {
		case "LIST":
			if dataOff+4 > len(data) {
				return w, h
			}
			listType := string(data[dataOff : dataOff+4])
			switch listType {
			case "movi":
				return w, h // media data starts — header done
			case "hdrl":
				avihW, avihH = scanHdrl(data, dataOff+4, dataOff+sz)
				if w == 0 {
					w, h = avihW, avihH
				}
			case "strl":
				sw, sh := scanStrl(data, dataOff+4, dataOff+sz)
				if sw > 0 && sh > 0 {
					w, h = sw, sh // strf is more reliable than avih
				}
			}
		}
		i += 8 + sz + (sz & 1)
	}
	return w, h
}

// scanHdrl scans LIST "hdrl" for "avih" to get dwWidth/dwHeight.
func scanHdrl(data []byte, off, end int) (w, h int) {
	for off+8 <= end {
		id := string(data[off : off+4])
		sz := int(binary.LittleEndian.Uint32(data[off+4 : off+8]))
		dataOff := off + 8
		if id == "avih" && sz >= 40 && dataOff+40 <= end {
			w = int(binary.LittleEndian.Uint32(data[dataOff+32 : dataOff+36]))
			h = int(binary.LittleEndian.Uint32(data[dataOff+36 : dataOff+40]))
			return w, h
		}
		if id == "LIST" && dataOff+4 <= end && string(data[dataOff:dataOff+4]) == "strl" {
			sw, sh := scanStrl(data, dataOff+4, dataOff+sz)
			if sw > 0 {
				return sw, sh
			}
		}
		off += 8 + sz + (sz & 1)
	}
	return 0, 0
}

// scanStrl scans LIST "strl" for "strf" (BITMAPINFOHEADER) to get biWidth/biHeight.
func scanStrl(data []byte, off, end int) (w, h int) {
	for off+8 <= end {
		id := string(data[off : off+4])
		sz := int(binary.LittleEndian.Uint32(data[off+4 : off+8]))
		dataOff := off + 8
		if id == "strf" && sz >= 20 && dataOff+20 <= end {
			bw := int(int32(binary.LittleEndian.Uint32(data[dataOff+4 : dataOff+8])))
			bh := int(int32(binary.LittleEndian.Uint32(data[dataOff+8 : dataOff+12])))
			if bh < 0 {
				bh = -bh
			}
			if bw > 0 && bh > 0 {
				return bw, bh
			}
		}
		off += 8 + sz + (sz & 1)
	}
	return 0, 0
}

// aviFirstFrame returns the first I-frame video payload from the movi chunk.
// Uses a proper RIFF walk (not a byte search) to locate the movi LIST.
func aviFirstFrame(data []byte) []byte {
	start, end := aviMoviRange(data)
	if start < 0 {
		return nil
	}
	p := start
	for p+8 <= end {
		sz := int(binary.LittleEndian.Uint32(data[p+4 : p+8]))
		if sz < 0 {
			break
		}
		if sz > 0 && p+8+sz <= len(data) && aviIsVideoCk(data[p:p+4]) {
			frame := data[p+8 : p+8+sz]
			if FrameType(frame) == picIntra {
				return frame
			}
		}
		p += 8 + sz + (sz & 1)
	}
	return nil
}

// aviMoviRange returns the byte range [start, end) of the movi chunk contents
// by walking the top-level RIFF structure (safe against false "movi" matches in metadata).
// The movi LIST may be larger than the data buffer; end is capped to len(data).
func aviMoviRange(data []byte) (start, end int) {
	i := 12 // skip "RIFF" + size + "AVI "
	for i+8 <= len(data) {
		id := string(data[i : i+4])
		sz := int(binary.LittleEndian.Uint32(data[i+4 : i+8]))
		if sz < 0 {
			break
		}
		dataOff := i + 8
		if id == "LIST" && dataOff+4 <= len(data) && string(data[dataOff:dataOff+4]) == "movi" {
			s := dataOff + 4
			e := dataOff + sz
			if e > len(data) {
				e = len(data)
			}
			return s, e
		}
		next := i + 8 + sz + (sz & 1)
		if next <= i {
			break
		}
		i = next
	}
	return -1, 0
}

func aviIsVideoCk(b []byte) bool {
	return len(b) >= 4 && b[0] == '0' && b[1] == '0' && (string(b[2:4]) == "dc" || string(b[2:4]) == "db")
}
