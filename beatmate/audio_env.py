"""Project-local audio configuration; never executes shell code from .env."""

import os
from pathlib import Path
import shlex
import tempfile

AUDIO_KEYS = {
    "BEATMATE_AUDIO_PROVIDER",
    "BEATMATE_AUDIO_LIVE",
    "BEATMATE_AUDIO_PROMPT_MODE",
    "BEATMATE_AUDIO_MAX_CANDIDATES",
    "BEATMATE_AUDIO_MAX_SUBMISSIONS",
    "MUREKA_API_KEY",
    "MUREKA_MODEL",
    "MUREKA_BASE_URL",
    "MUREKA_DOWNLOAD_HOSTS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
}


def load_audio_env(path=".env", environ=None):
    result = {}
    path = Path(path)
    if path.is_file():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, sep, value = line.partition("=")
            key = key.strip()
            if not sep or key not in AUDIO_KEYS:
                continue
            try:
                parts = shlex.split(value, comments=True, posix=True)
            except ValueError:
                raise ValueError(f".env 第{number}行格式错误") from None
            if len(parts) > 1:
                raise ValueError(f".env 第{number}行的值含空格，请加引号")
            result[key] = parts[0] if parts else ""
    result.update(os.environ if environ is None else environ)
    return result


def save_api_key(path, key):
    if not key or any(c.isspace() for c in key):
        raise ValueError("API Key不能为空或包含空白字符")
    path = Path(path)
    if path.is_symlink():
        raise ValueError("配置文件不能是符号链接")
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [
        line
        for line in lines
        if line.strip().removeprefix("export ").split("=", 1)[0].strip()
        != "MUREKA_API_KEY"
    ]
    lines.append("MUREKA_API_KEY=" + shlex.quote(key))
    fd, temp = tempfile.mkstemp(prefix=".env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
