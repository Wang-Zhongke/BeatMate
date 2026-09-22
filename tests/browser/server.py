"""Disposable offline browser fixture; never loads .env or starts a real provider."""
import argparse
from pathlib import Path
import sys
import tempfile
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from beatmate.audio import AudioService
from beatmate.api import server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8771);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='beatmate-browser-') as root:
        service=AudioService(root,env={'BEATMATE_AUDIO_MAX_CANDIDATES':'2'})
        for i in range(35):
            service.create('稀疏鼓组，温暖的吉他。',uuid.uuid4().hex,title=f'测试 Beat {i:02}',
                           creation_mode='song_stems' if i==0 else 'instrumental',
                           lyrics='[主歌]\n测试歌词' if i==0 else '',reviewed=True)
        for _ in range(4):service.tick()
        failed=service.create('失败记录测试',uuid.uuid4().hex,title='失败示例')
        failed.update(status='failed',error='测试连接失败，未提交生成。');service.save(failed)
        httpd=server(None,args.port,audio=service)
        print(f'http://127.0.0.1:{httpd.server_port}',flush=True)
        try:httpd.serve_forever()
        except KeyboardInterrupt:pass
        finally:httpd.server_close();service.close()

if __name__=='__main__':main()
