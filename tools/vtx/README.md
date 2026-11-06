# VTX stripifier native helper

`meshopt_stripify.dll` is a small C ABI wrapper around meshoptimizer's
stripify/unstripify API. It is built from meshoptimizer tag `v1.1`, commit
`dc9d09ed83e1004aef47a1c3c597e0ec64848a37`.

Pinned DLL SHA-256:

`A20DADA9544A7217B148BA73D0784041104DE08D26F7DD1AFFE6B7338807AF57`

Rebuild from the repository root after cloning that tag to
`build/vtx-meshoptimizer-v1.1`:

```powershell
g++ -std=c++11 -O2 -shared -static -static-libgcc -static-libstdc++ `
  -I build/vtx-meshoptimizer-v1.1/src `
  tools/vtx/meshopt_stripify_wrapper.cpp `
  build/vtx-meshoptimizer-v1.1/src/stripifier.cpp `
  -o tools/vtx/meshopt_stripify.dll
```

The pinned build was produced with MinGW-W64 GCC 16.1.0. The optimizer also
reparses every generated VTX and unstripifies its indices to compare the full
oriented triangle multiset before replacing the original file.
