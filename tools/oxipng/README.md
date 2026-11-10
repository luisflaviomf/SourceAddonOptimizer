# oxipng.exe (lossless PNG recompression)

Used by the worker command `pnglossless` (`png_lossless/`), bundled next to the worker by
`pyinstaller/worker.spec` (`_internal/oxipng.exe`).

- oxipng 10.2.0 from crates.io, default features, release, `x86_64-pc-windows-msvc`
  (rustc 1.89.0), MIT (`LICENSE.oxipng.txt`; upstream https://github.com/oxipng/oxipng).
- SHA-256 of this build: `e130aa0df8cb9f9c31519249e6a34b9f598e2333f90ecf23b9065942a9168de4`.
- Imports only `KERNEL32`, `VCRUNTIME140` and the universal CRT; the worker package ships
  `VCRUNTIME140.dll` in the same folder.

Rebuild:

```powershell
cargo install oxipng --version 10.2.0 --locked --root build/oxipng
Copy-Item build/oxipng/bin/oxipng.exe tools/oxipng/
python -m pytest tests/test_png_lossless.py
```

The worker never trusts the tool for the result: every file it writes is decoded again with Pillow and
compared pixel by pixel with the file it replaces (`png_lossless.same_pixels`).
