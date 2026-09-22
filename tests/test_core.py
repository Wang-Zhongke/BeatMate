import copy
import io
import json
import sqlite3
import tempfile
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import mido
from beatmate.model import BeatSpec, EditPlan, BAR, digest, validate_tracks
from beatmate.generator import generate
from beatmate.service import Service, ConflictError
from beatmate.render import midi_bytes, preview_bytes
from beatmate.adapters import AudioArtifact


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = Service(Path(self.tmp.name)/'state.sqlite3')
        self.v = self.service.create_project(text='90 BPM boom bap 8小节 C minor seed 7', protected_tracks=['kick', 'bass'])

    def edit(self, plan=None, base=None, **kwargs):
        return self.service.edit_project(self.v['project_id'], base or self.v['version_id'], plan or EditPlan('hihat',3,4,'density',32).to_dict(), **kwargs)

    def test_offline_determinism_and_style(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('Network forbidden')):
            other = self.service.create_project(spec=self.v['spec'])
            self.assertEqual(other['tracks'], self.v['tracks'])
            self.assertNotEqual(generate(BeatSpec(style='trap')), generate(BeatSpec()))
            self.assertTrue(midi_bytes(self.edit()).startswith(b'MThd'))

    def test_scope_and_protection_hashes(self):
        old_hash = digest(self.v)
        new = self.edit()
        self.assertNotEqual(new['version_id'], self.v['version_id'])
        self.assertEqual(new['parent_id'], self.v['version_id'])
        for a, b in zip(self.v['tracks'], new['tracks']):
            if a['id'] != 'hihat':
                self.assertEqual(a, b)
            else:
                outside = lambda t: [n for n in t['notes'] if not 2*BAR <= n['start_tick'] < 4*BAR]
                self.assertEqual(outside(a), outside(b))
                self.assertEqual(len(b['notes']) - len(a['notes']), 48)
        for check in new['audit']['protected_hashes'].values():
            self.assertEqual(check['before'], check['after'])
        self.assertEqual(digest(self.service.get_version(self.v['project_id'], self.v['version_id'])), old_hash)
        reopened = Service(self.service.db).get_version(self.v['project_id'])
        self.assertEqual(reopened, new)

    def test_rejections_leave_database_unchanged(self):
        bad = [EditPlan('kick',1,1,'mute',0).to_dict(),
               dict(track_id='hihat',start_bar=1,end_bar=9,operation='density',value=32),
               dict(track_id='hihat',start_bar=1,end_bar=2,operation='density',value=17),
               dict(track_id='snare',start_bar=1,end_bar=2,operation='velocity',value=126),
               dict(track_id='snare',start_bar=1,end_bar=2,operation='transpose',value=12),
               dict(track_id='hihat',start_bar=1,end_bar=2,operation='mute',value=0,execute='bad')]
        for plan in bad:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                self.edit(plan)
        with self.service.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM versions').fetchone()[0], 1)
        self.assertEqual(self.service.get_version(self.v['project_id']), self.v)

    def test_noop_new_version_and_stale_conflict(self):
        result = self.edit(EditPlan('hihat',1,1,'velocity',0).to_dict())
        self.assertEqual(result['tracks'], self.v['tracks'])
        self.assertNotEqual(result['version_id'], self.v['version_id'])
        with self.assertRaises(ConflictError):
            self.edit()

    def test_concurrent_compare_and_swap(self):
        def task(_):
            try:
                return self.edit()['version_id']
            except ConflictError:
                return 'conflict'
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(task, range(2)))
        self.assertEqual(results.count('conflict'), 1)
        with self.service.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM versions').fetchone()[0], 2)

    def test_persistent_protection_and_sql_immutability(self):
        new = self.edit(protected_tracks=['snare'])
        with self.assertRaises(ValueError):
            self.edit(EditPlan('snare',1,1,'mute',0).to_dict(), new['version_id'])
        with self.service.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE versions SET data=?', ('{}',))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM versions')

    def test_midi_roundtrip(self):
        v = self.edit()
        midi = mido.MidiFile(file=io.BytesIO(midi_bytes(v)))
        self.assertEqual((midi.type, midi.ticks_per_beat, len(midi.tracks)), (1,480,6))
        self.assertEqual(next(m.tempo for m in midi.tracks[0] if m.type=='set_tempo'), mido.bpm2tempo(90))
        signature = next(m for m in midi.tracks[0] if m.type=='time_signature')
        self.assertEqual((signature.numerator, signature.denominator), (4,4))
        for source, track in zip(v['tracks'], midi.tracks[1:]):
            self.assertEqual(track.name, source['name'])
            active, recovered, tick = {}, [], 0
            for message in track:
                tick += message.time
                if message.type in ('note_on','note_off'):
                    self.assertEqual(message.channel, source['channel'])
                    if message.type=='note_on' and message.velocity:
                        self.assertNotIn(message.note, active)
                        active[message.note] = (tick,message.velocity)
                    else:
                        start, velocity = active.pop(message.note)
                        recovered.append((message.note,start,tick-start,velocity))
            self.assertFalse(active)
            self.assertEqual(tick, 8*BAR)
            self.assertEqual(sorted(recovered), sorted((n['pitch'], n['start_tick'],n['duration_tick'],n['velocity']) for n in source['notes']))

    def test_preview(self):
        v = self.service.create_project(spec=dict(bars=1,bpm=120))
        before = digest(v)
        with wave.open(io.BytesIO(preview_bytes(v)), 'rb') as wav:
            self.assertEqual((wav.getnchannels(),wav.getsampwidth(),wav.getnframes()), (1,2,32000))
            self.assertNotEqual(set(wav.readframes(wav.getnframes())), {0})
        self.assertEqual(before,digest(v))

    def test_invalid_specs_and_pitch(self):
        for data in [dict(bars=0),dict(bpm=True),dict(swing=float('nan')),dict(mode='dorian'),dict(unknown=1)]:
            with self.assertRaises(ValueError):
                BeatSpec.parse(data)
        tracks=copy.deepcopy(self.v['tracks'])
        tracks[3]['notes'][0]['pitch']=128
        with self.assertRaises(ValueError):
            validate_tracks(tracks,BeatSpec.parse(self.v['spec']))

    def test_melodic_edit_and_mute(self):
        result = self.edit(EditPlan('chords',2,2,'transpose',12).to_dict())
        before = self.v['tracks'][4]['notes']
        after = result['tracks'][4]['notes']
        for a, b in zip(before, after):
            self.assertEqual(b['pitch']-a['pitch'], 12 if BAR <= a['start_tick'] < 2*BAR else 0)
            self.assertEqual(a['id'], b['id'])
        muted = self.edit(EditPlan('chords',2,2,'mute',0).to_dict(),result['version_id'])
        self.assertFalse(any(BAR <= n['start_tick'] < 2*BAR for n in muted['tracks'][4]['notes']))

    def test_boundary_crossing_note_rejected(self):
        parent = copy.deepcopy(self.v)
        parent['tracks'][4]['notes'][0]['duration_tick'] = BAR + 100
        with patch.object(self.service, 'get_version', return_value=parent):
            with self.assertRaisesRegex(ValueError, 'boundary-crossing'):
                self.edit(EditPlan('chords',2,2,'mute',0).to_dict())
        self.assertEqual(self.service.get_version(self.v['project_id']), self.v)

    def test_generation_extremes(self):
        for style in ('trap','boom_bap'):
            for swing in (0,.45):
                spec=BeatSpec(style=style,bars=32,bpm=40,swing=swing,key='B',seed=2**31-1)
                validate_tracks(generate(spec),spec)

    def test_audio_adapter_is_audio(self):
        artifact=AudioArtifact('sample.wav','future','pinned-version','source-id')
        self.assertEqual(artifact.kind,'audio')
        with self.assertRaises(TypeError):
            AudioArtifact('a','b','c','d',kind='midi')


if __name__ == '__main__':
    unittest.main()
