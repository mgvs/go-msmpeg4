/* vfwenc.c - BATCH VFW MPG4 encoder. Reads N concatenated grayscale frames (each W*H
   bytes), compresses each as an independent MPG4 keyframe with the DivX 3.11 / mpg4c32
   VFW codec, writes each bitstream length-prefixed (4-byte LE len + bytes) to output.
   One Wine startup -> N encodes (amortizes ~8s wine init). No VirtualDub, no GUI.
   Usage: vfwenc.exe <in_concat.gray> <W> <H> <out_concat.bin> <nframes> [quality]
   Compile: i686-w64-mingw32-gcc vfwenc.c -o vfwenc.exe -lvfw32 -lgdi32 */
#include <windows.h>
#include <vfw.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char** argv){
    if(argc<6){ fprintf(stderr,"usage: vfwenc in W H out nframes [q]\n"); return 2; }
    int W=atoi(argv[2]), H=atoi(argv[3]); int NF=atoi(argv[5]);
    int quality=(argc>6)?atoi(argv[6]):10000;
    long npix=(long)W*H;
    FILE* f=fopen(argv[1],"rb"); if(!f){fprintf(stderr,"open in fail\n");return 2;}
    FILE* o=fopen(argv[4],"wb"); if(!o){fprintf(stderr,"open out fail\n");return 2;}
    unsigned char* gray=malloc(npix);
    long rowsz=((W*3+3)/4)*4; long imgsz=rowsz*H;
    unsigned char* rgb=malloc(imgsz);

    BITMAPINFOHEADER bi; memset(&bi,0,sizeof bi);
    bi.biSize=sizeof bi; bi.biWidth=W; bi.biHeight=H; bi.biPlanes=1;
    bi.biBitCount=24; bi.biCompression=BI_RGB; bi.biSizeImage=imgsz;

    HIC hic=ICOpen(ICTYPE_VIDEO, mmioFOURCC('M','P','G','4'), ICMODE_COMPRESS);
    if(!hic){ fprintf(stderr,"ICOpen MPG4 fail\n"); return 1; }
    DWORD fmtsz=ICCompressGetFormatSize(hic,&bi);
    BITMAPINFOHEADER* obi=calloc(fmtsz>sizeof(BITMAPINFOHEADER)?fmtsz:sizeof(BITMAPINFOHEADER),1);
    if(ICCompressGetFormat(hic,&bi,obi)!=ICERR_OK){ fprintf(stderr,"GetFormat fail\n"); return 1; }
    DWORD maxout=ICCompressGetSize(hic,&bi,obi);
    void* outbuf=malloc(maxout);
    if(ICCompressBegin(hic,&bi,obi)!=ICERR_OK){ fprintf(stderr,"CompressBegin fail\n"); return 1; }

    int okc=0;
    for(int fr=0; fr<NF; fr++){
        if(fread(gray,1,npix,f)!=(size_t)npix){ fprintf(stderr,"short read at %d\n",fr); break; }
        memset(rgb,0,imgsz);
        for(int y=0;y<H;y++){ unsigned char* row=rgb+(long)(H-1-y)*rowsz;
            for(int x=0;x<W;x++){ unsigned char v=gray[(long)y*W+x]; unsigned char* p=row+x*3; p[0]=v;p[1]=v;p[2]=v; } }
        DWORD ckid=0,dwFlags=0;
        obi->biSizeImage=0;
        DWORD r=ICCompress(hic, (fr==0)?ICCOMPRESS_KEYFRAME:0, obi, outbuf, &bi, rgb,
                           &ckid, &dwFlags, 0, 0, quality, NULL, NULL);
        unsigned int len = (r==ICERR_OK)? obi->biSizeImage : 0;
        unsigned char hdr[4]={ len&0xff,(len>>8)&0xff,(len>>16)&0xff,(len>>24)&0xff };
        fwrite(hdr,1,4,o);
        if(len) fwrite(outbuf,1,len,o);
        if(len) okc++;
    }
    ICCompressEnd(hic); ICClose(hic);
    fclose(f); fclose(o);
    fprintf(stderr,"OK %d/%d frames encoded\n",okc,NF);
    return 0;
}
