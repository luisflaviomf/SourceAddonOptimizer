# gmo_native.dll (Rust hot paths of the worker)

Built from `native/gmo_native` (Rust, C ABI) and loaded with ctypes by `texlab/native.py`.
Bundled next to the worker executable by `pyinstaller/worker.spec`.

Every exported function is a drop-in replacement of a numpy routine and returns exactly the
same bytes. The numpy code stays as the reference and as the fallback when the DLL is missing
or disabled with `GMO_NATIVE=0`.

| Export | Replaces | Measured on the LVS cars pack |
|---|---|---|
| `gmo_decode_bc` | `texlab.bcnp.decode_bc` (DXT1/DXT3/DXT5) | 12,943 mip levels identical; 80.8 s → 3.4 s |
| `gmo_band_energy` | `texlab.allocate.band_energy_maps` (float32, numpy order) | 7,914 maps identical; 164.6 s → 28.6 s |
| `gmo_sample_map` | `texlab.allocate._sample_map` (float32 cumsum box blur) | 3,117 maps identical; 14.8 s → 2.7 s |
| `gmo_seams` | `model_screen.simplify._seams` (Models Tela, pass 1: seam chains) | 163 models x 2 profiles identical; `simplify_model` CPU 512 s → 60 s with the items below |
| `gmo_components` | `model_screen.simplify.components` (label = smallest node of the component) | identical labels (random graphs and the pack) |

Models: `_seams` walks chains in the iteration order of Python `set`s, so `seams.rs` emulates the
CPython 3.11 set table (same slots, probing, resize: `gmo_pyset_order` is the test hook). Its float
code follows numpy on the reference machine (OpenBLAS): length-3 dots are sequential without fused
multiply-add; the `(k,3) @ (3,)` product in Douglas-Peucker is `fma(a2,b2, fma(a0,b0, a1*b1))` for
k > 1 (dgemv), a plain dot for k = 1. numpy itself can round those differently on other CPUs; the
DLL gives the reference-machine result everywhere.

Float code keeps numpy's exact operation order: every Python constant is rounded to f64 then
to f32 (like numpy's weak scalars), and Rust never fuses multiply-adds. `ABI_VERSION` in
`src/lib.rs` and `texlab/native.py` must match; the loader ignores a DLL with another version.

Rebuild (Rust stable, x86_64-pc-windows-msvc):

```powershell
cargo test --release --manifest-path native/gmo_native/Cargo.toml
cargo build --release --manifest-path native/gmo_native/Cargo.toml
Copy-Item native/gmo_native/target/release/gmo_native.dll tools/native/
python -m pytest tests/test_native_texlab.py tests/test_native_models.py
```
