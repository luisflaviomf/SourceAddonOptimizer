# meshoptimizer simplifier DLL (model_screen)

`meshopt_simplify.dll` exports the plain meshoptimizer C API, built from the vendored
`third_party/meshoptimizer` (`v1.2 9d9890c73011d75920af614485296d1e03e95448`, MIT, see
`third_party/meshoptimizer/LICENSE.md`). It is loaded with ctypes by `model_screen/meshopt.py`
(the Models "Tela" mode) and bundled next to the worker executable.

Rebuild (Visual Studio 2022 x64 + CMake):

```powershell
cmake -S tools/meshopt -B build/meshopt -G "Visual Studio 17 2022" -A x64
cmake --build build/meshopt --config Release
Copy-Item build/meshopt/Release/meshopt_simplify.dll tools/meshopt/
```
