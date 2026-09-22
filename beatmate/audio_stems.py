"""Bounded ZIP parsing; provider filenames are never used as local paths."""
import io
from pathlib import PurePosixPath
import re
import stat
import zipfile
from .audio_provider import AudioError, MAX_AUDIO

MAX_STEM_ZIP = 100 * 1024 * 1024
MAX_STEM_TOTAL = 200 * 1024 * 1024


def split_archive(data):
    if not isinstance(data,bytes) or not 0 < len(data) <= MAX_STEM_ZIP:
        raise AudioError('分离文件包为空或过大')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members=archive.infolist()
            if len(members)>32 or sum(m.file_size for m in members)>MAX_STEM_TOTAL:
                raise AudioError('分离文件包超过解压限制')
            tracks={}
            for member in members:
                path=PurePosixPath(member.filename)
                mode=member.external_attr >> 16
                if (path.is_absolute() or '..' in path.parts or '\\' in member.filename or ':' in member.filename
                        or stat.S_ISLNK(mode) or member.flag_bits & 1):
                    raise AudioError('分离文件包含不安全路径或加密文件')
                if member.is_dir() or path.suffix.lower()!='.wav': continue
                if not 0 < member.file_size <= MAX_AUDIO: raise AudioError('分离音轨超过大小限制')
                name='/'.join(path.parts).lower()
                tokens=set(re.split(r'[^a-z]+',name))
                accompaniment=bool(tokens & {'instrumental','accompaniment'}) or '伴奏' in name or 'no_vocals' in name
                vocals=(bool(tokens & {'vocal','vocals','voice'}) or '人声' in name) and 'no_vocals' not in name
                if accompaniment == vocals: raise AudioError('分离文件包中音轨名称无法明确识别')
                role='instrumental' if accompaniment else 'vocals'
                if role in tracks: raise AudioError('分离文件包包含重复音轨')
                with archive.open(member) as stream:
                    audio=stream.read(MAX_AUDIO+1)
                if len(audio)!=member.file_size: raise AudioError('分离音轨内容不完整')
                tracks[role]=audio
            if set(tracks)!={'instrumental','vocals'}: raise AudioError('分离文件包缺少伴奏或人声音轨')
            return tracks
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError):
        raise AudioError('分离文件包损坏或格式不受支持') from None
