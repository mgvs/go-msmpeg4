// msmdump — extract the first video keyframe bytes from an AVI (MS-MPEG4) and
// print size + a hex/binary preview. Used to verify our demux against ffmpeg.
package main

import (
	"fmt"
	"os"

	"github.com/mgvs/go-mpeg4/riff"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: msmdump <file.avi> [outfile.bin]")
		os.Exit(2)
	}
	f, err := os.Open(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer f.Close()

	v, err := riff.ExtractFirstFrame(f)
	if err != nil {
		fmt.Fprintln(os.Stderr, "demux:", err)
		os.Exit(1)
	}
	fmt.Printf("fourcc=%s %dx%d sample=%d bytes extradata=%d\n",
		v.FourCC, v.Width, v.Height, len(v.Sample), len(v.Extradata))
	n := 24
	if n > len(v.Sample) {
		n = len(v.Sample)
	}
	fmt.Printf("first %d bytes: % x\n", n, v.Sample[:n])
	fmt.Printf("first 32 bits: ")
	for i := 0; i < 4 && i < len(v.Sample); i++ {
		fmt.Printf("%08b ", v.Sample[i])
	}
	fmt.Println()
	if len(os.Args) >= 3 {
		os.WriteFile(os.Args[2], v.Sample, 0644)
	}
}
