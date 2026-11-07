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
| 1 toolchain and foundations | done: rasm verified, arena, platform, renderer, crash reporter |
| 2 first million | done: 1M simulated and rendered; a 1M stress frame is 5.7 ms (175 FPS) on 16 threads |
| 3 swarm simulation | done: grid counting sort, capped separation, pursuit; bit-exact with the Python model at any thread count |
| 4 playable hero | done: WASD, mouse aim, automatic piercing weapon, damage, death, restart, escalating runs, top-down and third-person cameras (V), HUD. Camera obstruction waits for obstacles |
| 5 cybernetic aliens | assets done (Tripo, reviewed, 3 LODs, halved budgets, 6 in-place baked clips); only scuttlers are simulated; baked clips not yet played (VAT) |
| 6 combat spectacle | done: dash, gravity pulse, beam, orbital strike, death shards, camera shake, hit flash; progression: XP gems, levels, a pick of three from twelve upgrades, aliens toughen every 90 s, the swarm snowballs (quadratic). Balance needs real play |
| 7 optimisation | in progress: 1M tick on 16 threads 14.5 -> ~1.0 ms, one thread 31.6 -> 8.9 ms. Against clang-compiled C (docs/performance/contest.md): faster in 4 of 6 cells (every single-thread one, 100k at 16 threads), behind at 1M on 16 threads (0.73) and dense on 16 threads (0.84) |
| 8 polish | started: HDR, bloom, ACES, sky, basalt ground with glowing veins (the ground was never drawn before: culled), blob shadows, hero lamp and ring, pulse and strike on the ground, HUD with XP bar, level, timer and GPU time. Missing: audio, menus, settings, saves |

Controls: WASD move, mouse aim (fire is automatic), Space dash, right mouse
or Q gravity pulse, hold E beam, F orbital strike, V camera, wheel zoom, R
restart, Esc quit; on level up 1, 2, 3 or a click picks an upgrade. `--autoplay` plays the bench input schedule.

Commercial release gates: none passed. Gate 1 (fun prototype) needs the
abilities and real playtesting; nothing here has been played by anyone
outside this session. Steam items (Steamworks, store assets, account) are
untouched and partly need the author.

## Simulation performance (Ryzen 9 9950X3D)

| entities | threads | us per tick |
|---|---|---|
| 100k | 1 | 1375 |
| 1M | 1 | 14472 |
| 100k | 16 | 695 |
| 1M | 16 | 5130 |

SIM 4/5 rules (combat and abilities included). Everything runs every tick for every entity; nothing is
approximated or skipped (sim_spec.md section 7).

## Open issues

- Asset licence: Tripo grants rights to paid accounts only; the account tier
  could not be confirmed through the API. Recorded as unverified in
  shared/assets/provenance.json. Must be confirmed before any release.
- The ground and horizon are too dark to read in third person.
- Hero dies in seconds when standing still at high density: balance needs the
  ability kit (dash, pulse) and tuning by real play.
