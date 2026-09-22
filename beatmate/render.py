import io
import math
import random
import struct
import wave
from array import array
import mido
from .model import PPQ, BAR


def midi_bytes(version):
    spec = version["spec"]
    midi = mido.MidiFile(type=1, ticks_per_beat=PPQ)
    meta = mido.MidiTrack()
    midi.tracks.append(meta)
    meta.extend(
        [
            mido.MetaMessage("track_name", name="BeatMate tempo"),
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(spec["bpm"])),
            mido.MetaMessage("time_signature", numerator=4, denominator=4),
            mido.MetaMessage("end_of_track", time=spec["bars"] * BAR),
        ]
    )
    for source in version["tracks"]:
        track = mido.MidiTrack()
        midi.tracks.append(track)
        channel = source["channel"]
        track.append(mido.MetaMessage("track_name", name=source["name"]))
        track.append(
            mido.Message("program_change", channel=channel, program=source["program"])
        )
        events = []
        for n in source["notes"]:
            events.append(
                (
                    n["start_tick"],
                    1,
                    mido.Message(
                        "note_on",
                        channel=channel,
                        note=n["pitch"],
                        velocity=n["velocity"],
                    ),
                )
            )
            events.append(
                (
                    n["start_tick"] + n["duration_tick"],
                    0,
                    mido.Message(
                        "note_off", channel=channel, note=n["pitch"], velocity=0
                    ),
                )
            )
        last = 0
        for tick, _, message in sorted(events, key=lambda e: (e[0], e[1], e[2].note)):
            track.append(message.copy(time=tick - last))
            last = tick
        track.append(mido.MetaMessage("end_of_track", time=spec["bars"] * BAR - last))
    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def preview_bytes(version, sample_rate=16000):
    """Reference synth driven exclusively by the native note snapshot."""
    seconds_per_tick = 60 / version["spec"]["bpm"] / PPQ
    length = round(version["spec"]["bars"] * BAR * seconds_per_tick * sample_rate)
    mix = array("f", [0]) * length
    rng = random.Random(0)
    for track in version["tracks"]:
        for n in track["notes"]:
            start = round(n["start_tick"] * seconds_per_tick * sample_rate)
            duration = n["duration_tick"] * seconds_per_tick
            kind = track["id"]
            duration = (
                min(duration, 0.15 if kind == "hihat" else 0.25)
                if kind in ("kick", "snare", "hihat")
                else duration
            )
            count = min(round(duration * sample_rate), length - start)
            frequency = 440 * 2 ** ((n["pitch"] - 69) / 12)
            for i in range(count):
                t = i / sample_rate
                if kind == "kick":
                    sample = math.sin(
                        2 * math.pi * (48 * t + 3 * (1 - math.exp(-30 * t)))
                    ) * math.exp(-18 * t)
                elif kind in ("snare", "hihat"):
                    sample = rng.uniform(-1, 1) * math.exp(
                        -(35 if kind == "hihat" else 20) * t
                    )
                else:
                    envelope = min(1, t / 0.008) * min(1, (duration - t) / 0.025)
                    sample = (
                        math.sin(2 * math.pi * frequency * t)
                        * envelope
                        * (0.65 if kind == "bass" else 0.3)
                    )
                mix[start + i] += sample * n["velocity"] / 127 * 0.22
    pcm = bytearray(length * 2)
    for i, sample in enumerate(mix):
        struct.pack_into("<h", pcm, i * 2, round(math.tanh(sample) * 30000))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()
