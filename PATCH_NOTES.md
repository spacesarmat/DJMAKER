# Patch 0013 — Windows MinGW std::byte conflict

- Windows Essentia cross-build switched from C++17 to C++14.
- Windows `djmaker-essentia` bridge also builds as C++14.
- macOS remains C++17 (ARM64 pipeline already passed build, bridge and self-test).
- Runtime revision bumped to `2026.08.27-66a890f2-r5`.
- Added regression coverage so Windows does not silently return to C++17.

Reason: MinGW-w64 WinAPI headers define global `byte`, while C++17 defines `std::byte`. Some upstream Essentia headers use `using namespace std;` before including Windows headers, causing ambiguous `byte` references. C++14 is the upstream Essentia default and avoids this conflict without patching upstream source headers.
