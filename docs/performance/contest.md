# Contest: hand-written assembly against clang

The question (2026-11-06): can the assembly engine beat clang on any CPU?
The method: an independent agent implemented SIM 5 in C in
`C:\devel\alien-rival`, from the spec, the Python reference model and the
harness only, without seeing this engine or its log. Rules: clang 22,
`-O3 -march=x86-64-v3 -ffp-contract=off`, no fast-math, LTO/PGO allowed,
AVX2 intrinsics allowed, no inline assembly, no AVX-512, no thread pinning.
The referee: the state hash must equal the model's bit for bit, at any thread
count; the contest's `tools/bench.py` drives both executables identically.

## Round 1 (2026-11-07)

Median us per tick, 3 fresh processes after a warmup, back to back on the
Ryzen 9 9950X3D, every hash OK. The assembly engine is at f3571d6 (with the
binned grid and the parallel compaction of the same day).

| scenario | C 1 thread | asm 1 thread | C 16 threads | asm 16 threads |
|---|---:|---:|---:|---:|
| 100k sparse | 1012 | 1434 | 110 | 502 |
| 1M sparse | 9268 | 15328 | 733 | 3572 |
| 1M dense (spawn half 300) | 22535 | 84999 | 1920 | 7186 |

The C engine wins every cell: 1.4x to 3.8x on one thread, 3.7x to 4.9x on
16. Its own plain transcription of the spec (`simple.exe`) runs 1M sparse at
75.6 ms on one thread, so both optimised engines are far from naive; the gap
between them is design, not code generation.

## Why it wins (from its NOTES.md)

1. **Entities live in grid order**, sorted by (cell, creation sequence). The
   spec's index order is creation order (stable compaction, append-only
   spawns), so the hash sorts by sequence once, at the end. Separation and
   integration then stream memory in order; nothing is gathered.
2. **The grid is rebuilt from last tick's order**: most entities keep their
   cell, so a band only merges the few that moved, instead of a full counting
   sort of a million entities.
3. **One fused pass per band**: rebuild a band's rows into per-thread row
   buffers that stay in cache, separate and integrate each row as soon as
   the next row exists. Each entity is touched once per tick from L1/L2.
4. **Lanes are entities**: 8 or 16 consecutive entities of a row are the
   lanes, each candidate of the row window is broadcast to all of them. A
   candidate outside a lane's 3x3 cells is at least 2 m away, so each lane
   sums exactly its spec neighbours in spec order, branch free. Dense
   separation went from ~320 to ~130 cycles per entity.
5. **The cap is lazy**: the 16-neighbour cap is applied only to a group in
   which some lane exceeded it.
6. Area effects scan only the cells they can reach; work stealing over runs of
   bands at 16 threads.

## What it means

The prediction before the contest was that the algorithm and the data layout
hold most of the speed and are available to either language; that held, and
by more than expected. The assembly engine's per-instruction control did not
compensate for a layout that gathers through `sorted_id` and rebuilds the
grid from scratch every tick.

Round 2, as agreed before the contest, is to steal: the ideas above are not
specific to C, and porting them is the honest way to find out what assembly
adds on top of the same design.

## Round 2, in progress

| step | 100k 1T | 100k 16T | 1M 1T | 1M 16T | dense 1T | dense 16T |
|---|---:|---:|---:|---:|---:|---:|
| round 1 (asm) | 1434 | 502 | 15328 | 3572 | 84999 | 7186 |
| lanes = entities separation | 1139 | 491 | 16410 | 3554 | 40349 | 4151 |
| storage in grid order | 1255 | 442 | 16434 | 3004 | 40700 | 4407 |
| separation + integration fused | 1228 | 400 | 15907 | 2752 | 40569 | 3857 |
| the dead left to the next build | 1282 | 357 | 15639 | 2481 | 41504 | 3928 |
| incremental rebuild | 1391 | 375 | 14880 | 2390 | 42030 | 3974 |
| fused band pass (adaptive) | 1492 | 365 | 15511 | 2419 | 41195 | 4074 |
| dead left to the build, incremental rebuild, windowed sweep, area by region, cells from integration | 1575 | 293 | 16039 | 1896 | 42215 | 3732 |
| separation fast path (no self test, lazy cap) | 1366 | 286 | 13498 | 1768 | 33070 | 3024 |
| AVX2 band prefix, chunk affinity in the pool | 1394 | 168 | 13444 | 1330 | 32204 | 3520 |
| per-row cap fallback, all-capped exit | 1312 | 137 | 12184 | 1455 | 31142 | 2979 |
| clang's loop shape, vector cell counts, longer spin, vector stayer counts | 1184 | 134 | 10753 | 1118 | 27989 | 2883 |
| merge build, steering early, boundary rows in the band job | 1037 | 123 | 9493 | 1006 | 26433 | 2671 |
| C rival | 1012 | 110 | 9268 | 733 | 22535 | 1920 |

All six ideas are now in the assembly engine, each exact (every hash
passes). Round 2 closed the gap from 1.4-4.9x to 1.4-3.3x: one thread is
within 1.5-1.9x everywhere, 16 threads 2.1-3.3x.

**Can assembly beat clang here?** Not with this engine's tick structure. The
C engine's remaining advantage is the shape of a tick: one work-stealing pass
that builds each band from the previous tick's neighbouring rows and
separates it in cache, with no global bin and bucket passes and no barrier
between phases. Our tick is six pool phases, each reading its inputs from
L3/DRAM, and at 16 threads it is memory-bound. Instruction-level work on top
of that was worth single-digit percent per step today. Matching that shape is
a rewrite of the simulation's scheduling, not a port of one more kernel; after
it, the honest expectation for assembly is parity to a 10-30% edge on the
compute-bound parts (dense separation), not a multiple.

## Round 2, interleaved (2026-11-07, late)

`tools/duel.py`: the engines alternate per cell, medians of 5 (the machine's
load drifted during the day, so separate runs were no longer comparable).

| scenario | asm us | C us | C / asm |
|---|---:|---:|---:|
| 100k, 1 thread | 1046 | 1037 | 0.99 |
| 100k, 16 threads | 106 | 112 | **1.06** |
| 1M, 1 thread | 9510 | 9308 | 0.98 |
| 1M, 16 threads | 915 | 734 | 0.80 |
| 1M dense, 1 thread | 26615 | 22956 | 0.86 |
| 1M dense, 16 threads | 2581 | 1942 | 0.75 |

The first cell won by the assembly engine, two level, three behind.
