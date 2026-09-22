"""Original bounded musical patterns. Versioned separately from the legacy generator."""
import copy
import random
from .creative import CreativeSpec, CreativeEditPlan, GENERATOR_VERSION
from .generator import note, hats
from .model import BAR, KEYS, validate_tracks

SCALES = {'minor': (0,2,3,5,7,8,10), 'major': (0,2,4,5,7,9,11)}
PROGRESSIONS = {'descending': (0,6,5,6), 'resolving': (0,5,2,4)}


def generate_creative(spec):
    a = spec.arrangement
    tracks = [dict(id=x,name=x,channel=9 if i<3 else i-3,
                   program=32 if x=='bass' else 0,notes=[]) for i,x in enumerate(spec.track_ids)]
    by_id = {t['id']:t for t in tracks}
    scale = SCALES[spec.mode]
    tonic = KEYS.index(spec.key)
    motif = random.Random(f'{spec.seed}:motif:v2').choice(((0,2,4,2,0),(2,0,4,2,0),(0,4,2,0,2)))
    velocity = {'soft':68,'medium':88,'strong':108}[a.drum_velocity]

    def tone(degree, octave):
        return octave + tonic + scale[degree%7] + 12*(degree//7)

    def add(track, bar, tick, pitch, duration, strength):
        by_id[track]['notes'].append(note(track,bar,tick,pitch,duration,strength,spec.seed))

    for bar in range(spec.bars):
        chord = PROGRESSIONS[a.harmony][bar%4]
        lift = a.section_variation=='lift' and (bar//4)%2==1
        rng = random.Random(f'{spec.seed}:drums:{bar}:v2')
        kick = [0] if a.drum_density=='sparse' else ([0,720,1200] if spec.style=='boom_bap' else [0,660,1440])
        snare = [480,1440] if spec.style=='boom_bap' else [960]
        hat_count = 4 if a.drum_density=='sparse' else 8
        hat_ticks = [i*(BAR//hat_count) + (round((BAR//hat_count)*spec.swing) if i%2 else 0) for i in range(hat_count)]
        if lift:
            kick.append(1680)
            if bar%4==3:
                snare.append(1680)
        for track,ticks,pitch in [('kick',kick,36),('snare',snare,38),('hihat',hat_ticks,42)]:
            for tick in ticks:
                strength = velocity + (6 if lift else 0) + rng.randint(-3,3) - (18 if track=='hihat' else 0)
                add(track,bar,tick,pitch,60 if track=='hihat' else 90,strength)
        bass_ticks = (0,) if a.bass_presence=='low' else (0,960)
        for tick in bass_ticks:
            add('bass',bar,tick,tone(chord,36),600 if a.bass_presence=='low' else 840,65 if a.bass_presence=='low' else 88)
        chord_pitches = [tone(chord+i,48) for i in (0,2,4)]
        if a.chord_style=='block':
            for pitch in chord_pitches:
                add('chords',bar,0,pitch,960 if a.vocal_space=='roomy' else 1680,58)
        else:
            ticks = (0,480,960) if a.vocal_space=='roomy' else (0,480,960,1440)
            for i,tick in enumerate(ticks):
                add('chords',bar,tick,chord_pitches[i%3],360,60 if i==0 else 53)
        if a.melody_enabled:
            positions = (0,720,1440) if a.melody_density=='sparse' else (0,360,720,1080,1440)
            if a.vocal_space=='roomy':
                positions = tuple(t for t in positions if t<1440)
            for i,tick in enumerate(positions):
                degree = motif[(i+bar%2)%len(motif)]
                if a.melody_variation=='moderate' and bar%4==3 and i>0:
                    degree = (degree+2)%5
                if lift and i==len(positions)-1:
                    degree = (degree+2)%5
                pitch = tone(chord+degree,60)
                while pitch>83:
                    pitch-=12
                # Every bar begins on a chord tone; the rest stay in the scale.
                add('melody',bar,tick,pitch,240 if len(positions)>3 else 360,69 if i==0 else 61)
    for track in tracks:
        track['notes'].sort(key=lambda n:(n['start_tick'],n['pitch']))
    validate_tracks(tracks,spec,expected_track_ids=spec.track_ids)
    return tracks


def thin_notes(notes, start, end):
    """Keep each bar's first (anchor) note and even-indexed motif notes."""
    selected = [n for n in notes if start<=n['start_tick']<end]
    counts, keep = {}, set()
    for n in sorted(selected,key=lambda n:(n['start_tick'],n['pitch'])):
        bar = n['start_tick']//BAR
        index = counts.get(bar,0)
        if index%2==0:
            keep.add(n['id'])
        counts[bar] = index+1
    removed = [n['id'] for n in selected if n['id'] not in keep]
    if not removed:
        raise ValueError('No removable melody notes; anchors are preserved')
    return [n for n in notes if not start<=n['start_tick']<end or n['id'] in keep], removed


def reproduce(version):
    if version.get('generator_version')!=GENERATOR_VERSION:
        raise ValueError('Unknown generator version')
    spec = CreativeSpec.parse(version['spec'])
    tracks = generate_creative(spec)
    for item in version.get('edit_history',[]):
        p=CreativeEditPlan.parse(item['plan'])
        target=next(t for t in tracks if t['id']==p.track_id)
        start,end=(p.start_bar-1)*BAR,p.end_bar*BAR
        if p.operation=='thin_notes':
            target['notes'],_=thin_notes(target['notes'],start,end)
        elif p.operation in ('mute','density'):
            target['notes']=[n for n in target['notes'] if not start<=n['start_tick']<end]
            if p.operation=='density':
                for bar in range(p.start_bar-1,p.end_bar):
                    target['notes'].extend(hats(spec,bar,p.value))
        else:
            for n in target['notes']:
                if start<=n['start_tick']<end:
                    n['velocity' if p.operation=='velocity' else 'pitch']+=p.value
        target['notes'].sort(key=lambda n:(n['start_tick'],n['pitch']))
    validate_tracks(tracks,spec,expected_track_ids=spec.track_ids)
    return tracks


def explain(version):
    """Descriptions are derived locally from stored choices and actual events."""
    a=version['spec']['arrangement']
    descriptions={
        'harmony':f"调内和声级数 {PROGRESSIONS[a['harmony']]}（从0计数），影响和弦、bass和旋律音高。",
        'chord_style': '分解和弦逐音进入。' if a['chord_style']=='arpeggio' else '柱式和弦同时进入。',
        'melody_enabled': '生成独立melody轨。' if a['melody_enabled'] else '未生成melody轨。',
        'melody_density': '基础每小节3个位置。' if a['melody_density']=='sparse' else '基础每小节5个位置。',
        'melody_variation': '四小节末改变部分动机音高。' if a['melody_variation']=='moderate' else '重复两小节动机，不加额外四小节末变奏。',
        'drum_density': '基础每小节1个kick和4个hihat。' if a['drum_density']=='sparse' else '基础每小节3个kick和8个hihat。',
        'drum_velocity': f"鼓基础力度{ {'soft':68,'medium':88,'strong':108}[a['drum_velocity']] }；hihat更轻。",
        'section_variation':'每隔四小节增添kick、提高鼓力度并变化句末旋律；乐句末加snare。' if a['section_variation']=='lift' else '各段不附加推动型变奏。',
        'bass_presence':'每小节1个较短较轻bass音。' if a['bass_presence']=='low' else '每小节2个更长、力度更高的bass音。',
        'vocal_space':'移除旋律最后一拍位置；和弦缩短或减少末拍分解音，给人声留时隙。' if a['vocal_space']=='roomy' else '保留末拍旋律与较完整和弦时值。',
        'timbre_hint':'仅为制作建议，参考WAV仍使用基础合成器，不还原该乐器。'}
    notes={t['id']:len(t['notes']) for t in version['tracks']}
    inactive = not a['melody_enabled']
    choices=[]
    for name,value in a.items():
        status='advice_only' if name=='timbre_hint' else 'inactive' if inactive and name in ('melody_density','melody_variation') else 'applied'
        choices.append(dict(parameter=name,value=value,status=status,effect=descriptions[name]))
    return dict(user_expressed=version['creative_brief']['explicit'],applied_choices=choices,
                actual_note_counts=notes, scoped_edits=copy.deepcopy(version.get('edit_history',[])),
                note='说明来自保存规格及实际音符；局部编辑见scoped_edits。未进行人工听感确认，不保证伤感或共鸣。')
