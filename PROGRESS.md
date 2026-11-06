# MILLION DEAD: progress

A swarm game in hand-written x86-64 assembly (rasm, the assembler inside the
rin compiler) on Direct3D 11. The C++ engine was dropped at the author's
direction (2026-11-06): the work is the assembly engine.

## Build, test, run

    python tools/build.py                 # shaders (fxc) + engine (rin asm) -> build/md_rasm.exe
    python tools/test.py                  # self tests (plain and guard pages) + bit-exact reference hashes
    python tools/bench.py --note "..."    # reproducible simulation benchmark, appended to benchmarks/results/history.csv
    build/md_rasm.exe                     # play: WASD, mouse aim, V camera, wheel zoom, R restart, Esc quit
    build/md_rasm.exe --entities 1000000 --spawn-half 1000    # stress scene: a million at once

Diagnostics: `--diag` (guard page after every arena allocation), crash
reports with a stack scan (`python tools/symbolize.py <rva>`),
`--capture f.bmp` with `--frames N --hidden`, `--dump-state f` with
`python tools/state_diff.py` (first differing value per column).

## Where it stands

| milestone | state |
|---|---|
| 1 toolchain and foundations | done: rasm verified (D3D11 example builds and runs), arena, platform, renderer |
| 2 first million | done: 1M entities simulated and rendered (AVX2 culling, 3 LODs, instancing) |
| 3 swarm simulation | done: grid counting sort, capped separation, pursuit, integration; bit-exact with a Python model |
| 4 playable hero | mostly: WASD, mouse aim, automatic piercing weapon, damage, death, restart, escalating runs, top-down and third-person cameras with V blending. Missing: HUD (stats are in the title bar), camera obstruction handling (no obstacles exist yet) |
| 5 cybernetic aliens | assets done (Tripo, reviewed, 3 LODs, v2 budgets); only scuttlers are in the simulation |
| 6 combat spectacle | not started: dash, gravity pulse, beam, bombardment, explosions, progression |
| 7 optimisation | started: separation 2.7x (see docs/performance/log.md). Threads not started |
| 8 polish | not started: ground/sky look, audio, UI, menus, settings, saves |

Commercial release gates: none passed. Gate 1 (fun prototype) needs the
abilities and real playtesting; nothing here has been played by anyone
outside this session. Steam items (Steamworks, store assets, account) are
untouched and partly need the author.

## Simulation performance (single thread, Ryzen 9 9950X3D)

| entities | us per tick | grid | separate | integrate (cycles/entity) |
|---|---|---|---|---|
| 100k | 1353 | 19.4 | 36.7 | 1.9 |
| 1M | 17188 | 33.0 | 37.9 | 2.8 |

These were measured at SIM 3. A 1M stress frame is ~36 ms before the
separation change. Everything runs every tick for every entity; nothing is
approximated or skipped (sim_spec.md section 7).

## Open issues

- Asset licence: Tripo grants rights to paid accounts only; the account tier
  could not be confirmed through the API. Recorded as unverified in
  shared/assets/provenance.json. Must be confirmed before any release.
- The ground and horizon are too dark to read in third person.
- Hero dies in seconds when standing still at high density: balance needs the
  ability kit (dash, pulse) and tuning by real play.
