"""Validate provider MIDI archives without extracting provider paths to disk."""

import io
from pathlib import PurePosixPath
import stat
import struct
import zipfile
import mido
from .audio_provider import AudioError

MAX_MIDI_ZIP = 20 * 1024 * 1024
MAX_MIDI_FILE = 5 * 1024 * 1024
MAX_MIDI_TOTAL = 20 * 1024 * 1024


def validate_midi(data):
    if len(data) < 14 or data[:8] != b"MThd\x00\x00\x00\x06":
        raise AudioError("MIDI文件头无效")
    kind, tracks, division = struct.unpack(">HHH", data[8:14])
    if (
        kind not in (0, 1, 2)
        or not 1 <= tracks <= 256
        or (kind == 0 and tracks != 1)
        or division == 0
    ):
        raise AudioError("MIDI轨道信息无效")
    offset = 14
    for _ in range(tracks):
        if data[offset : offset + 4] != b"MTrk" or offset + 8 > len(data):
            raise AudioError("MIDI轨道不完整")
        size = int.from_bytes(data[offset + 4 : offset + 8], "big")
        offset += 8 + size
        if offset > len(data):
            raise AudioError("MIDI轨道不完整")
    if offset != len(data):
        raise AudioError("MIDI内容长度无效")
    parsed = mido.MidiFile(file=io.BytesIO(data))
    if any(not track or track[-1].type != "end_of_track" for track in parsed.tracks):
        raise AudioError("MIDI轨道未完整结束")


def midi_archive(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_MIDI_ZIP:
        raise AudioError("MIDI文件包为空或过大")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 32 or sum(m.file_size for m in members) > MAX_MIDI_TOTAL:
                raise AudioError("MIDI文件包超过解压限制")
            files = []
            names = set()
            for member in members:
                path = PurePosixPath(member.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in member.filename
                    or ":" in member.filename
                    or len(member.filename) > 256
                    or any(ord(c) < 32 for c in member.filename)
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or member.flag_bits & 1
                ):
                    raise AudioError("MIDI文件包路径不安全或已加密")
                if member.is_dir():
                    continue
                # Never offer a ZIP containing arbitrary executable or non-MIDI payloads.
                if path.suffix.lower() not in (".mid", ".midi"):
                    raise AudioError("MIDI文件包包含非MIDI文件")
                if path.as_posix().casefold() in names:
                    raise AudioError("MIDI文件包包含重复路径")
                names.add(path.as_posix().casefold())
                if not 0 < member.file_size <= MAX_MIDI_FILE:
                    raise AudioError("MIDI文件大小无效")
                with archive.open(member) as stream:
                    content = stream.read(MAX_MIDI_FILE + 1)
                if len(content) != member.file_size:
                    raise AudioError("MIDI文件不完整")
                validate_midi(content)
                files.append((path.as_posix(), content))
            if not files:
                raise AudioError("文件包内没有MIDI文件")
            return files
    except (
        zipfile.BadZipFile,
        RuntimeError,
        OSError,
        ValueError,
        EOFError,
        KeyError,
        IndexError,
        NotImplementedError,
    ):
        raise AudioError("MIDI文件包损坏或格式不受支持") from None
