"""Independent reference model of shared/specs/sim_spec.md (SIM 3).

Pure Python, written from the spec, not from the assembly. Every f32
operation is computed in double precision and rounded to f32: for + - * / and
sqrt of f32 operands, double has enough precision that this double rounding
equals the correctly rounded f32 result, so the model is exact.

    python tools/sim_reference.py --entities 1000 --ticks 120 --seed 1
prints the state hash, which must equal `md_rasm.exe --bench-sim` with the
same arguments. Slow: meant for small populations.
"""
import argparse
import math
import struct

M64 = (1 << 64) - 1
_f = struct.Struct('<f')


def f32(x):
    return _f.unpack(_f.pack(x))[0]


DT = f32(1.0 / 60.0)
assert struct.pack('<f', DT) == bytes.fromhex('8988883c')
WORLD_HALF, INV_CELL, GRID_DIM = 1024.0, 0.5, 1024
SEP_MAX = 16
SEP_STRENGTH, STEER, SPEED, HERO_SPEED = 6.0, 0.125, 7.0, 9.0
DIAG = f32(0.70710677)
LO, HI = -1023.5, 1023.5
EPS = f32(1.0e-4)
BENCH_KEYS = [1, 8, 4, 2]


class Pcg:
    def __init__(self, seed):
        self.state, self.inc = 0, ((seed << 1) | 1) & M64
        self.next()
        self.state = (self.state + seed) & M64
        self.next()

    def next(self):
        old = self.state
        self.state = (old * 6364136223846793005 + self.inc) & M64
        x = (((old >> 18) ^ old) >> 27) & 0xffffffff
        r = old >> 59
        return ((x >> r) | (x << ((-r) & 31))) & 0xffffffff


def add(a, b): return f32(a + b)
def sub(a, b): return f32(a - b)
def mul(a, b): return f32(a * b)
def div(a, b): return f32(a / b)


def clamp(v):
    return min(max(v, LO), HI)


class Sim:
    def __init__(self, count, seed, spawn_half=1000.0):
        rng = Pcg(seed)
        self.count, self.tick = count, 0
        self.hx = self.hz = 0.0
        self.px, self.pz = [0.0] * count, [0.0] * count
        self.vx, self.vz = [0.0] * count, [0.0] * count
        for i in range(count):
            a = mul(float(rng.next() >> 8), f32(5.9604645e-08))
            b = mul(float(rng.next() >> 8), f32(5.9604645e-08))
            self.px[i] = mul(sub(mul(a, 2.0), 1.0), spawn_half)
            self.pz[i] = mul(sub(mul(b, 2.0), 1.0), spawn_half)

    def step(self, keys):
        ix = float((1 if keys & 8 else 0) - (1 if keys & 2 else 0))
        iz = float((1 if keys & 1 else 0) - (1 if keys & 4 else 0))
        if ix != 0 and iz != 0:
            ix, iz = mul(ix, DIAG), mul(iz, DIAG)
        self.hx = clamp(add(self.hx, mul(mul(ix, HERO_SPEED), DT)))
        self.hz = clamp(add(self.hz, mul(mul(iz, HERO_SPEED), DT)))
        n = self.count
        # grid
        cells = {}
        cell = [0] * n
        for i in range(n):
            cx = int(mul(add(self.px[i], WORLD_HALF), INV_CELL))
            cz = int(mul(add(self.pz[i], WORLD_HALF), INV_CELL))
            cell[i] = (cx, cz)
            cells.setdefault((cx, cz), []).append(i)   # ascending i: stable
        # separation
        sep = [(0.0, 0.0)] * n
        for i in range(n):
            px, pz = self.px[i], self.pz[i]
            cx, cz = cell[i]
            sx = sz = 0.0
            k = 0
            done = False
            for oz in (-1, 0, 1):
                for ox in (-1, 0, 1):
                    c = (cx + ox, cz + oz)
                    if not (0 <= c[0] < GRID_DIM and 0 <= c[1] < GRID_DIM):
                        continue
                    for j in cells.get(c, ()):
                        if j == i:
                            continue
                        dx, dz = sub(px, self.px[j]), sub(pz, self.pz[j])
                        d2 = add(mul(dx, dx), mul(dz, dz))
                        if d2 < 1.0:
                            w = sub(1.0, d2)
                            sx, sz = add(sx, mul(dx, w)), add(sz, mul(dz, w))
                            k += 1
                            if k == SEP_MAX:
                                done = True
                                break
                    if done:
                        break
                if done:
                    break
            sep[i] = (sx, sz)
        # integrate
        for i in range(n):
            dx, dz = sub(self.hx, self.px[i]), sub(self.hz, self.pz[i])
            d2 = add(mul(dx, dx), mul(dz, dz))
            if d2 > EPS:
                inv = div(1.0, f32(math.sqrt(d2)))
                tx, tz = mul(mul(dx, inv), SPEED), mul(mul(dz, inv), SPEED)
            else:
                tx = tz = 0.0
            tx = add(tx, mul(sep[i][0], SEP_STRENGTH))
            tz = add(tz, mul(sep[i][1], SEP_STRENGTH))
            vx = add(self.vx[i], mul(sub(tx, self.vx[i]), STEER))
            vz = add(self.vz[i], mul(sub(tz, self.vz[i]), STEER))
            self.px[i] = clamp(add(self.px[i], mul(vx, DT)))
            self.pz[i] = clamp(add(self.pz[i], mul(vz, DT)))
            self.vx[i], self.vz[i] = vx, vz
        self.tick += 1

    def bench_keys(self):
        return BENCH_KEYS[(self.tick // 120) & 3]

    def hash(self):
        h = 0xcbf29ce484222325
        data = struct.pack('<IIff', self.count, self.tick, self.hx, self.hz)
        for col in (self.px, self.pz, self.vx, self.vz):
            data += struct.pack(f'<{self.count}f', *col)
        for b in data:
            h = ((h ^ b) * 0x100000001b3) & M64
        return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--entities', type=int, default=1000)
    ap.add_argument('--ticks', type=int, default=120)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--spawn-half', type=int, default=1000)
    a = ap.parse_args()
    s = Sim(a.entities, a.seed, float(a.spawn_half))
    for _ in range(a.ticks):
        s.step(s.bench_keys())
    print(f'hash={s.hash():016x}')


if __name__ == '__main__':
    main()
