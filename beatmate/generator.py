import random
from .model import BAR, KEYS, TRACK_IDS, digest


def note(track, bar, tick, pitch, duration, velocity, seed):
    return dict(id=digest([track, bar, tick, pitch, seed])[:24], pitch=pitch,
                start_tick=bar * BAR + tick, duration_tick=duration, velocity=velocity)


def hats(spec, bar, count):
    rng = random.Random(f'{spec.seed}:hihat:{bar}:{count}')
    step = BAR // count
    return [note('hihat', bar, i * step + (round(step * spec.swing) if i % 2 else 0),
                 42, min(60, step // 2), rng.randint(58, 88), spec.seed) for i in range(count)]


def generate(spec):
    tracks = [dict(id=x, name=x, channel=9 if i < 3 else i-3,
                   program=32 if x == 'bass' else 0, notes=[]) for i, x in enumerate(TRACK_IDS)]
    root = 36 + KEYS.index(spec.key)
    for bar in range(spec.bars):
        rng = random.Random(f'{spec.seed}:{bar}')
        kick = (0, 720, 1200) if spec.style == 'boom_bap' else (0, 660, 1440, 1680)
        snare = (480, 1440) if spec.style == 'boom_bap' else (960,)
        for track, positions, pitch in [(tracks[0], kick, 36), (tracks[1], snare, 38)]:
            track['notes'].extend(note(track['id'], bar, tick, pitch, 90, rng.randint(95, 115), spec.seed) for tick in positions)
        tracks[2]['notes'].extend(hats(spec, bar, 8 if spec.style == 'boom_bap' else 16))
        degree = (0, 0, 5, 7)[bar % 4]
        for tick in (0, 960):
            tracks[3]['notes'].append(note('bass', bar, tick, root + degree, 840, rng.randint(90, 105), spec.seed))
        for interval in (0, 3 if spec.mode == 'minor' else 4, 7):
            tracks[4]['notes'].append(note('chords', bar, 0, root + 24 + degree + interval, 1680, 64, spec.seed))
    return tracks
