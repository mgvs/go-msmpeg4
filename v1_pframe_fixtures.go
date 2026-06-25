package msmpeg4

// v1 I+P test fixtures: MS-MPEG4 v1 (MP41) frame payloads, 96x64, encoded by the
// original mpg4c32.dll, with varying (non-uniform) motion so the left-only MV predictor is
// exercised (the H.263 median would mis-predict). Used by TestV1PFrame.
var v1PFrames = []string{
	"AAABAABA/2PwG2g4P5oeXB8GC3C0Z6pjE08PysMrr9PjNZ8PpZM66Zyi3VXBiBaAttY+Fs4WwLdB9Q/r6DgBL1Me8sDghOxWmPBb0Cg9Tj6m/Hrw+LDNQW7oZrC0PRr9BmUzdTIWZ9p9a+wOp+kWXx2NgwHzLHt4BNFFu1kyH64BO9/OAbvMhRz4C12CiBa58uG0WOsdm84Ongu5yoBjsCzkJguD3V+sW5n6DIfeF+zXc/7teuDW2ufPXWHgza1R4ng7R2Np1+OxtOD4FTuMmd1FTuUAUH//gA==",
	"AAABAAo3Ba++++r/DIvAKXBps09qtuc5Jq3ItWXG3iWJUOtAsxQ9t7mtyu96phSsfA4f8rLgcP+cFk9tZx99XLxKNl4+Ng==",
	"AAABABIlgUZ1K2970SI2fOJuepCGTm8aosIIkGzoQQhQHC/mASjR9jwgbzr6uAaAcqoBIMB5AgafZBKAixzGcZ3Ofa9Nq4IIIAOH/AwHkCAbbBEFU+5hbTuSZw89ySKwDQDKdeEEISs6",
	"AAABABonBRMNnm8+++iwDwDgcP+eAeAcDh/rYKJztvvt7lcA0AyA4X6AeAcDh/rYLIBD2++t0Vq4B4QAcP9APAOBw/5wWvvvvq4QQgUHC/hLEr+P",
	"AAABACImgRJzTZ9999XBgO4GAEPb8hBgOoGAEoOyFwRJ232MnfVwYDuBgBARyEGA8AYANBw/5wRBTUJOPrNhm6SIYYSxIoOF+vCCEKA4X84JX3331cA8IFOhDEp4",
}
