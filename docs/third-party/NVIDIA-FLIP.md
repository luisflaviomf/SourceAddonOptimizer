# NVIDIA FLIP used by Compress Maximum

Compress Maximum embeds the official CPU implementation of NVIDIA FLIP 1.7 so
the published WPF application applies the same perceptual gate used by the VTF
experiment, without requiring Python or a GPU.

- Upstream: https://github.com/NVlabs/flip
- Source commit: `b475eb4bf394ab877c42166c9eb0a84a02cc5b14`
- Backend: pure C++ (`src/cpp/tool/CPP.vcxproj`), Release x64, Visual Studio 2022
- Embedded binary SHA-256: `F5CCAFB7D56ED081FE2BD2AA236B1F8A28C8EAD6C29600CAC61ACEEDABFDE439`

The local build adds one machine-readable output line containing the existing
mean and maximum values plus an unweighted NumPy-compatible 95th percentile of
the official FLIP error map. It does not alter the FLIP evaluation itself.

The upstream BSD-3-Clause license and its third-party notices are included in
`Resources/VtfMaximumTools.zip` under `licenses/`.
