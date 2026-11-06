"""Run a bench scenario in the engine and the reference model and report the
first differing value of every part of the state.

    python tools/state_diff.py --entities 4000 --ticks 400 --seed 7 --spawn-half 150
"""
import argparse
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import sim_reference  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--entities', type=int, default=1000)
    ap.add_argument('--ticks', type=int, default=120)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--spawn-half', type=int, default=1000)
    a = ap.parse_args()
    path = Path(tempfile.gettempdir()) / 'md_state.bin'
    subprocess.run([str(ROOT / 'build/md_rasm.exe'), '--bench-sim', '--entities', str(a.entities), '--ticks', str(a.ticks),
                    '--seed', str(a.seed), '--spawn-half', str(a.spawn_half), '--dump-state', str(path)], check=True,
                   capture_output=True)
    b = path.read_bytes()
    count, tick, pc, kills = struct.unpack_from('<4I', b, 0)
    hx, hz, hhp = struct.unpack_from('<3f', b, 16)
    alive, shots, cool, spawned = struct.unpack_from('<4I', b, 28)
    off = 48
    eng = {}
    for name in ('px', 'pz', 'vx', 'vz', 'hp'):
        eng[name] = list(struct.unpack_from(f'<{count}f', b, off))
        off += 4 * count
    for name in ('proj_x', 'proj_z', 'proj_vx', 'proj_vz'):
        eng[name] = list(struct.unpack_from(f'<{pc}f', b, off))
        off += 4 * pc
    for name in ('proj_life', 'proj_pierce'):
        eng[name] = list(struct.unpack_from(f'<{pc}I', b, off))
        off += 4 * pc

    s = sim_reference.Sim(a.entities, a.seed, float(a.spawn_half))
    for _ in range(a.ticks):
        s.bench_step()
    ref = {'px': s.px, 'pz': s.pz, 'vx': s.vx, 'vz': s.vz, 'hp': s.hp,
           'proj_x': [p[0] for p in s.proj], 'proj_z': [p[1] for p in s.proj],
           'proj_vx': [p[2] for p in s.proj], 'proj_vz': [p[3] for p in s.proj],
           'proj_life': [p[4] for p in s.proj], 'proj_pierce': [p[5] for p in s.proj]}
    scalars = [('count', count, s.count), ('tick', tick, s.tick), ('proj_count', pc, len(s.proj)),
               ('kills', kills, s.kills), ('hero_x', hx, s.hx), ('hero_z', hz, s.hz), ('hero_hp', hhp, s.hero_hp),
               ('hero_alive', alive, s.hero_alive), ('shots', shots, s.shots), ('fire_cooldown', cool, s.cool),
               ('spawned', spawned, s.spawned)]
    bad = 0
    for name, e, r in scalars:
        if e != r:
            bad += 1
            print(f'DIFF {name}: engine {e!r} reference {r!r}')
    for name, col in eng.items():
        rc = ref[name]
        if len(col) != len(rc):
            bad += 1
            print(f'DIFF {name}: length engine {len(col)} reference {len(rc)}')
            continue
        for i, (e, r) in enumerate(zip(col, rc)):
            if e != r:
                bad += 1
                print(f'DIFF {name}[{i}]: engine {e!r} reference {r!r}')
                break
    print('identical' if not bad else f'{bad} part(s) differ')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
