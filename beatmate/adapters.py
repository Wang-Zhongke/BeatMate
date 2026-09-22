"""Future audio integrations cannot masquerade as native MIDI projects."""
from dataclasses import dataclass, field
from typing import Protocol, Literal
from .model import BeatSpec


@dataclass(frozen=True)
class AudioArtifact:
    path: str
    provider: str
    provider_version: str
    source_version_id: str
    kind: Literal['audio'] = field(default='audio', init=False)


class AudioAdapter(Protocol):
    def render(self, spec: BeatSpec, source_version_id: str) -> AudioArtifact: ...

# Legacy render boundary retained. Audio MVP uses InstrumentalAdapter below.


class InstrumentalAdapter(Protocol):
    """Independent async audio path; no BeatSpec, notes or source MIDI required."""
    provider: str
    model: str
    def submit(self, task: dict) -> dict: ...
    def query(self, task: dict) -> dict: ...
    def download(self, choice: dict) -> bytes: ...
