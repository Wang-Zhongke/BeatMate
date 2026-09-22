"""One-command local startup; saves an API key only on the explicit launcher path."""
import argparse
import getpass
import os
from pathlib import Path
import sys
from .audio_env import load_audio_env, save_api_key
from .audio_health import DEFAULT_PORT


def main(argv=None):
    parser=argparse.ArgumentParser(prog='beatmate-start',description='启动 BeatMate 本地音乐工作台')
    parser.add_argument('--port',type=int,default=DEFAULT_PORT)
    parser.add_argument('--audio-dir',default='.beatmate/audio')
    args=parser.parse_args(argv)
    try: env=load_audio_env()
    except ValueError as error: parser.error(str(error))
    if env.get('BEATMATE_AUDIO_PROVIDER')=='mureka':
        saved=load_audio_env(environ={}).get('MUREKA_API_KEY')
        key=env.get('MUREKA_API_KEY')
        if not key:
            if not sys.stdin.isatty():
                raise SystemExit('请在Terminal中运行启动脚本，首次需要输入Mureka API Key。')
            print('首次启动：API Key将保存在本项目.env，仅当前用户可读写。')
            key=getpass.getpass('请输入Mureka API Key（输入不显示）：').strip()
        if not saved or key!=saved: save_api_key(Path('.env'),key)
        env['MUREKA_API_KEY']=key
    from .cli import main as cli_main
    previous=sys.argv
    empty_key_override = (env.get('BEATMATE_AUDIO_PROVIDER')=='mureka'
                          and os.environ.get('MUREKA_API_KEY')=='')
    try:
        # An explicitly empty export must not mask the key just entered and saved.
        if empty_key_override: os.environ['MUREKA_API_KEY']=env['MUREKA_API_KEY']
        sys.argv=['beatmate','audio-serve','--port',str(args.port),'--audio-dir',args.audio_dir]
        cli_main()
    finally:
        sys.argv=previous
        if empty_key_override: os.environ['MUREKA_API_KEY']=''


if __name__=='__main__': main()
