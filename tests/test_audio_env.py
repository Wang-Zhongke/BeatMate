import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from beatmate.audio_env import load_audio_env, save_api_key


class AudioEnvTests(unittest.TestCase):
    def test_literal_values_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'.env'
            p.write_text("MUREKA_MODEL=mureka-9\nexport MUREKA_API_KEY='literal-$HOME-$(not-executed)'\nBEATMATE_AUDIO_PROVIDER=mureka\nSSL_CERT_FILE=/etc/ssl/cert.pem\nIGNORED=value\n")
            env=load_audio_env(p,{'BEATMATE_AUDIO_PROVIDER':'mock'})
            self.assertEqual(env['MUREKA_API_KEY'],'literal-$HOME-$(not-executed)')
            self.assertEqual(env['BEATMATE_AUDIO_PROVIDER'],'mock')
            self.assertEqual(env['SSL_CERT_FILE'],'/etc/ssl/cert.pem')
            self.assertNotIn('IGNORED',env)
    def test_key_saved_privately_without_changing_settings(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'.env';p.write_text('MUREKA_MODEL=mureka-9\nMUREKA_API_KEY=old\n')
            save_api_key(p,'test-secret')
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            self.assertEqual(load_audio_env(p,{})['MUREKA_API_KEY'],'test-secret')
            self.assertEqual(load_audio_env(p,{})['MUREKA_MODEL'],'mureka-9')
            with self.assertRaises(ValueError):save_api_key(p,'bad\nkey')
            link=Path(root)/'link';link.symlink_to(p)
            with self.assertRaises(ValueError):save_api_key(link,'replacement')
    def test_launcher_asks_once_and_reuses_saved_key(self):
        from beatmate.audio_start import main
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'.env';p.write_text('BEATMATE_AUDIO_PROVIDER=mureka\nMUREKA_API_KEY=\n')
            old=os.getcwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ,{},clear=True),patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',return_value='test-key') as ask,patch('beatmate.cli.main') as start,patch('builtins.print'),patch('sys.argv',[]):
                    main();self.assertEqual(start.call_count,1);self.assertEqual(ask.call_count,1)
                    self.assertEqual(load_audio_env(p,{})['MUREKA_API_KEY'],'test-key')
                    self.assertNotIn('MUREKA_API_KEY',os.environ)
                with patch.dict(os.environ,{},clear=True),patch('getpass.getpass') as ask,patch('beatmate.cli.main') as start,patch('sys.argv',[]):
                    main();ask.assert_not_called();start.assert_called_once()
                with patch.dict(os.environ,{'MUREKA_API_KEY':''},clear=True),patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',return_value='replacement-key'),patch('beatmate.cli.main',side_effect=lambda:self.assertEqual(os.environ['MUREKA_API_KEY'],'replacement-key')),patch('sys.argv',[]),patch('builtins.print'):
                    main()
                    self.assertEqual(os.environ['MUREKA_API_KEY'],'')
                    self.assertEqual(load_audio_env(p,{})['MUREKA_API_KEY'],'replacement-key')
            finally: os.chdir(old)
    def test_parser_errors_do_not_expose_secret_values(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'.env';p.write_text("MUREKA_API_KEY='secret-no-close")
            with self.assertRaises(ValueError) as caught:load_audio_env(p,{})
            self.assertNotIn('secret',str(caught.exception))
