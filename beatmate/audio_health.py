"""Local runtime diagnostics. Network checks stop after the TLS handshake."""

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import socket
import ssl
import threading
from .audio_provider import public_target

DEFAULT_PORT = 8767
UI_VERSION = 5


def source_revision():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(
        [
            *root.glob("*.py"),
            root / "audio.html",
            *root.joinpath("ui").glob("*.js"),
            *root.joinpath("ui").glob("*.css"),
        ]
    ):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def config_stamp(path):
    try:
        return hashlib.sha256(path.read_bytes()).digest()
    except FileNotFoundError:
        return None


class RuntimeHealth:
    def __init__(self):
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.revision = source_revision()
        self.config_path = Path(".env").resolve()
        self.config_stamp = config_stamp(self.config_path)
        self.network_lock = threading.Lock()

    def report(self, service):
        changed = source_revision() != self.revision
        config_changed = config_stamp(self.config_path) != self.config_stamp
        decoder = shutil.which("ffmpeg") or (
            "/usr/bin/afconvert" if Path("/usr/bin/afconvert").is_file() else None
        )
        checks = [
            dict(
                name="音乐服务配置",
                status="error" if service.adapter.errors() else "ok",
                detail="；".join(service.adapter.errors())
                or (
                    "离线测试模式"
                    if service.adapter.provider == "mock"
                    else "本机配置完整；账户权限尚未验证"
                ),
            ),
            dict(
                name="音频解码器",
                status="ok" if decoder else "warning",
                detail=Path(decoder).name
                if decoder
                else "仅支持 PCM WAV 校验；其他格式请安装 ffmpeg",
            ),
            dict(
                name="运行版本",
                status="warning" if changed or config_changed else "ok",
                detail="代码或配置已更新，请停止原服务后重新启动。"
                if changed or config_changed
                else "运行版本与本地文件一致",
            ),
        ]
        # Validate CA configuration locally; never expose values or credentials.
        try:
            ssl.create_default_context()
            checks.append(
                dict(
                    name="可信证书",
                    status="ok",
                    detail="证书配置可加载；远端证书需通过连接检查确认",
                )
            )
        except (OSError, ssl.SSLError):
            checks.append(
                dict(
                    name="可信证书",
                    status="error",
                    detail="无法加载可信 CA，请检查 SSL_CERT_FILE / SSL_CERT_DIR",
                )
            )
        return dict(
            ui_version=UI_VERSION,
            revision=self.revision,
            started_at=self.started_at,
            restart_required=changed or config_changed,
            audio_dir=str(service.root),
            config_file=str(self.config_path),
            checks=checks,
            environment_overrides=[
                k
                for k in (
                    "BEATMATE_AUDIO_PROVIDER",
                    "MUREKA_MODEL",
                    "MUREKA_API_KEY",
                    "BEATMATE_AUDIO_MAX_SUBMISSIONS",
                    "SSL_CERT_FILE",
                    "SSL_CERT_DIR",
                )
                if k in os.environ
            ],
            proxy="当前直接连接，不读取浏览器或 HTTP_PROXY / HTTPS_PROXY 设置。",
        )

    def network(self, service):
        if service.adapter.provider == "mock":
            return dict(
                status="skipped",
                stage="offline",
                detail="离线模式无需连接供应商。",
                http_sent=False,
            )
        if not self.network_lock.acquire(blocking=False):
            return dict(
                status="busy",
                stage="checking",
                detail="连接检查正在进行，请稍候。",
                http_sent=False,
            )
        raw = secured = None
        stage = "dns"
        try:
            parsed, address = public_target("https://api.mureka.ai", ("api.mureka.ai",))
            stage = "tcp"
            raw = socket.create_connection((address, 443), timeout=5)
            stage = "tls"
            secured = ssl.create_default_context().wrap_socket(
                raw, server_hostname=parsed.hostname
            )
            return dict(
                status="ok",
                stage="tls",
                detail="DNS、TCP 与 TLS 连接正常。未发送 HTTP 请求；账户权限、余额和下载域名尚未验证。",
                http_sent=False,
            )
        except Exception:
            detail = {
                "dns": "域名解析或公网地址校验失败，请检查 DNS。",
                "tcp": "无法连接服务端口，请检查网络或代理连接方式。",
                "tls": "安全连接或证书校验失败，请检查网络和可信 CA。",
            }[stage]
            return dict(status="error", stage=stage, detail=detail, http_sent=False)
        finally:
            if secured is not None:
                secured.close()
            elif raw is not None:
                raw.close()
            self.network_lock.release()


def startup_summary(service):
    report = service.health.report(service)
    lines = [f"音乐库：{report['audio_dir']}", f"配置文件：{report['config_file']}"]
    lines.extend(f"{c['name']}：{c['detail']}" for c in report["checks"])
    return "\n".join(lines)
