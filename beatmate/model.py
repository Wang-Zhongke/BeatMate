from dataclasses import dataclass, asdict
import hashlib
import json
from typing import TypedDict

PPQ = 480
BAR = PPQ * 4
TRACK_IDS = ('kick', 'snare', 'hihat', 'bass', 'chords')
KEYS = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


class Note(TypedDict):
    id: str
    pitch: int
    start_tick: int
    duration_tick: int
    velocity: int


class Track(TypedDict):
    id: str
    name: str
    channel: int
    program: int
    notes: list[Note]


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} must be integer {low}..{high}')


def fields(data, allowed, required=()):
    if not isinstance(data, dict) or set(data) - set(allowed) or set(required) - set(data):
        raise ValueError('Missing or unknown fields')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class BeatSpec:
    style: str = 'boom_bap'
    bpm: int = 90
    bars: int = 8
    key: str = 'C'
    mode: str = 'minor'
    swing: float = 0.12
    seed: int = 0

    def __post_init__(self):
        if self.style not in ('boom_bap', 'trap') or self.key not in KEYS or self.mode not in ('minor', 'major'):
            raise ValueError('Unsupported style, key or mode')
        integer(self.bpm, 40, 220, 'bpm')
        integer(self.bars, 1, 32, 'bars')
        integer(self.seed, 0, 2**31 - 1, 'seed')
        if type(self.swing) not in (int, float) or not 0 <= self.swing <= .45:
            raise ValueError('swing must be 0..0.45')

    @classmethod
    def parse(cls, data):
        fields(data, cls.__dataclass_fields__)
        return cls(**data)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class EditPlan:
    track_id: str
    start_bar: int
    end_bar: int
    operation: str
    value: int

    def __post_init__(self):
        if self.track_id not in TRACK_IDS:
            raise ValueError('Unknown track_id')
        integer(self.start_bar, 1, 32, 'start_bar')
        integer(self.end_bar, self.start_bar, 32, 'end_bar')
        if self.operation == 'velocity':
            integer(self.value, -126, 126, 'velocity delta')
        elif self.operation == 'transpose':
            integer(self.value, -24, 24, 'semitones')
            if self.track_id in TRACK_IDS[:3]:
                raise ValueError('Cannot transpose drum mappings')
        elif self.operation == 'density':
            if self.track_id != 'hihat' or type(self.value) is not int or self.value not in (8, 16, 32):
                raise ValueError('density only supports hihat: 8,16,32 notes/bar')
        elif self.operation == 'mute':
            integer(self.value, 0, 0, 'mute value')
        else:
            raise ValueError('Unsupported edit operation')

    @classmethod
    def parse(cls, data):
        fields(data, cls.__dataclass_fields__, cls.__dataclass_fields__)
        return cls(**data)

    def to_dict(self):
        return asdict(self)


def validate_tracks(tracks, spec, expected_track_ids=TRACK_IDS):
    if [t['id'] for t in tracks] != list(expected_track_ids):
        raise ValueError('Invalid track set')
    ids = set()
    for track in tracks:
        ends = {}
        for n in sorted(track['notes'], key=lambda x: x['start_tick']):
            if n['id'] in ids:
                raise ValueError('Duplicate note id')
            ids.add(n['id'])
            integer(n['pitch'], 0, 127, 'pitch')
            integer(n['velocity'], 1, 127, 'velocity')
            integer(n['start_tick'], 0, spec.bars * BAR - 1, 'start_tick')
            integer(n['duration_tick'], 1, spec.bars * BAR - n['start_tick'], 'duration_tick')
            if ends.get(n['pitch'], 0) > n['start_tick']:
                raise ValueError('Overlapping same-pitch notes')
            ends[n['pitch']] = n['start_tick'] + n['duration_tick']
