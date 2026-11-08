"""Run every automated check against build/md_rasm.exe. Exit non-zero on any failure.

    python tools/test.py            # self tests (normal and guard-page builds) + reference hashes
    python tools/test.py --quick    # skip the slower reference scenarios

Each check prints one line; a check that cannot run counts as a failure, so a
filtered or truncated log can never look like a pass.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get('MD_EXE', ROOT / 'build' / 'md_rasm.exe'))  # MD_EXE: test another build
sys.path.insert(0, str(ROOT / 'tools'))
import sim_reference  # noqa: E402

SELF_TESTS = 7
SCENARIOS = [  # (entities, ticks, seed, spawn_half): sparse, dense, very dense with long hero walk
    (1000, 120, 1, 1000),
    (3000, 300, 5, 20),
    (2000, 600, 11, 8),
    (4000, 400, 7, 150),        # hero survives: live projectiles in the hash
    (4000, 700, 7, 150),        # every ability, twice: dash, pulse, beam, bomb strike
]
QUICK = SCENARIOS[:2]
# large scenarios the Python model is too slow for: hashes recorded from
# engine runs that matched the model's scenarios at every thread count, and
# matched independently by the C rival (C:\devellien-rival). They cover
# what small runs miss: band boundaries, the windowed build, dense crowds.
LARGE = [  # (entities, ticks, seed, spawn_half, hash)
    (100000, 600, 1, 1000, 'ab8666237ff52a1c'),
    (1000000, 300, 1, 1000, '7a1a32542db8e5ab'),
    (1000000, 300, 1, 300, '9f1e580c29fd4f94'),
]


def run(args):
    r = subprocess.run([str(EXE), *args], capture_output=True, text=True, timeout=600)
    return r.returncode, r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true')
    a = ap.parse_args()
    failures = 0

    def check(name, ok, detail=''):
        nonlocal failures
        print(f"{'PASS' if ok else 'FAIL'} {name}{(': ' + detail) if detail and not ok else ''}", flush=True)
        failures += 0 if ok else 1

    if not EXE.is_file():
        check('executable exists', False, str(EXE))
        sys.exit(1)
    for extra, label in (([], 'self tests'), (['--diag'], 'self tests with guard pages')):
        code, out = run(['--test', *extra])
        passes = out.count('PASS ')
        check(f'{label} ({passes}/{SELF_TESTS})', code == 0 and passes == SELF_TESTS, out.strip())
    for n, t, seed, half in (QUICK if a.quick else SCENARIOS):
        args = ['--entities', str(n), '--ticks', str(t), '--seed', str(seed), '--spawn-half', str(half)]
        s = sim_reference.Sim(n, seed, float(half))
        for _ in range(t):
            s.bench_step()
        want = f'{s.hash():016x}'
        for threads in ('1', '16'):
            code, out = run(['--bench-sim', '--threads', threads, *args])
            got = next((l.split()[0].split('=', 1)[1] for l in out.splitlines() if l.startswith('hash=')), None)
            check(f'reference hash n={n} ticks={t} seed={seed} spawn_half={half} threads={threads}',
                  code == 0 and got == want, f'engine {got} reference {want}')
    if not a.quick:
        for n, t, seed, half, want in LARGE:
            args = ['--entities', str(n), '--ticks', str(t), '--seed', str(seed), '--spawn-half', str(half)]
            for threads in ('1', '16'):
                code, out = run(['--bench-sim', '--threads', threads, *args])
                got = next((l.split()[0].split('=', 1)[1] for l in out.splitlines() if l.startswith('hash=')), None)
                check(f'recorded hash n={n} ticks={t} spawn_half={half} threads={threads}',
                      code == 0 and got == want, f'engine {got} recorded {want}')
    print(f'{failures} failure(s)')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
