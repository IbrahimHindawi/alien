# rasm as used here

The engine is built by `rin asm` from `C:\devel\rin-windows-x64\rin.exe`
(the packaged compiler; set `RIN` to use another). No change to rin or rasm
was needed so far. Notes from building a 4,000-line engine with it:

## What worked as specified

- AVX2 throughout: `ymm` registers, three-operand VEX forms, `vcmpps` with a
  numeric predicate, `vblendvps`, `vmovmskps`, BMI1/2 (`tzcnt`, `blsr`,
  `andn`), `vmovntps` streaming stores. The emitted `.s` is assembled by
  clang, so any instruction clang knows is available.
- Typed globals and `[reg].Struct.field` operands; the generated
  `_Static_assert`s caught nothing wrong, which is the point.
- `[N]const f32[align(32)]` constant tables for full-width vector loads.
- PDB line tables: `llvm-symbolizer` maps an address in md_rasm.exe to a
  `.rasm` line, which is what tools/symbolize.py does.

## Traps hit (all programmer errors the assembler cannot see)

- **Argument order in `invoke`/`vcall`**: `vcall(context, Map, rcx, ...)`
  passes the context as the resource, because the macro moves the object into
  rcx first. Found twice (Map, DrawIndexedInstanced). Rule: never pass rcx,
  or a register an earlier argument is moved into.
- **Frame slots**: three times a scratch slot was placed at or above the
  frame's top (over saved registers or the return address). Every proc header
  now states its arithmetic (`8 + pushes + sub = multiple of 16`) and the
  offsets it owns.
- **Floats by position**: `ClearDepthStencilView`'s depth is the 4th argument,
  so it goes in xmm3, not r9.

## Limitations that shaped the engine

- **No unwind tables** (by design, rasm spec R8). Windows cannot walk a rasm
  stack, so `SetUnhandledExceptionFilter` is never reached: crashes are
  caught by a vectored exception handler (platform/crash.rasm) that prints
  the fault and scans the stack for return addresses in the image.
- **Debuggers**: lldb from LLVM 22 here fails to start (missing
  python311.dll); cdb produced no output in this environment. The crash
  handler plus symbolize.py replaced them.
- **No conditional assembly**: diagnostic variants are run-time switches
  (`--diag`) rather than separate builds.
- `#define` lines take no `;` comment.

## Toolchain versions

clang 22.1.6, Windows SDK 10.0.26100.0 (fxc), Python 3.13, Blender 5.1 (assets).
