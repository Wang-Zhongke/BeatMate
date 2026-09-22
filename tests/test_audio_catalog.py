import json
import tempfile
import unittest
import uuid
from unittest.mock import patch
from beatmate.audio import AudioService

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=AudioService(self.tmp.name,env={'BEATMATE_AUDIO_MAX_CANDIDATES':'2'});self.addCleanup(self.s.close)
        for i in range(7):self.s.create('描述',uuid.uuid4().hex,title=f'Beat {i}',n=2 if i==0 else 1)
        for _ in range(4):self.s.tick()

    def test_pages_stable_without_duplicate_candidates_and_verify_only_page_files(self):
        with patch.object(self.s,'file',wraps=self.s.file) as read:
            page=self.s.catalog_page(limit=3,sort='oldest')
            self.assertEqual(len(page['assets']),3);self.assertEqual(read.call_count,3)
        pages=[self.s.catalog_page(offset=i,limit=3,sort='oldest') for i in (0,3,6)]
        ids=[aid for p in pages for aid in p['page']['ids']]
        self.assertEqual(len(ids),8);self.assertEqual(len(set(ids)),8)
        self.assertEqual(pages[0]['assets'][0]['version_count'],2)
        self.assertEqual(self.s.catalog_page(offset=999,limit=3)['page']['offset'],6)

    def test_search_favorite_and_trash_apply_to_whole_library_and_literal_wildcards(self):
        page=self.s.catalog_page(search='bEaT 0',limit=1)
        self.assertEqual(page['page']['total'],2)
        aid=page['page']['ids'][0]
        self.s.library_update([aid],'rename',title='Moon_%灯')
        self.assertEqual(self.s.catalog_page(search='_%')['page']['ids'],[aid])
        self.s.library_update([aid],'favorite',favorite=True)
        self.assertEqual(self.s.catalog_page(view='favorites')['page']['ids'],[aid])
        self.s.library_update([aid],'trash')
        self.assertEqual(self.s.catalog_page(view='favorites')['page']['total'],0)
        self.assertEqual(self.s.catalog_page(view='trash')['page']['ids'],[aid])
        self.s.library_update([aid],'purge')
        self.assertEqual(self.s.catalog_page(view='trash')['page']['total'],0)

    def test_pinned_playback_is_returned_but_never_added_to_visible_page(self):
        first=self.s.catalog_page(limit=1,sort='oldest');aid=first['page']['ids'][0]
        second=self.s.catalog_page(offset=3,limit=1,sort='oldest',include_ids=[aid])
        self.assertNotIn(aid,second['page']['ids'])
        self.assertIn(aid,[a['id'] for a in second['assets']])
        self.s.library_update([aid],'trash')
        second=self.s.catalog_page(offset=3,limit=1,include_ids=[aid])
        self.assertTrue(next(m for m in second['library'] if m['id']==aid)['deleted_at'])
        self.s.library_update([aid],'purge')
        self.assertNotIn(aid,[a['id'] for a in self.s.catalog_page(include_ids=[aid])['assets']])

    def test_revision_is_persistent_changes_on_mutation_and_poll_does_not_read_files(self):
        before=self.s.catalog_revision();self.assertFalse(before['active'])
        with patch.object(self.s,'file',side_effect=AssertionError('poll read file')):
            self.assertEqual(before,self.s.catalog_revision())
        aid=self.s.catalog_page(limit=1)['page']['ids'][0]
        self.s.library_update([aid],'rename',title='new')
        after=self.s.catalog_revision();self.assertGreater(after['revision'],before['revision'])
        other=AudioService(self.tmp.name,env={});self.addCleanup(other.close)
        self.assertEqual(other.catalog_revision()['revision'],after['revision'])
        self.s.create('新任务',uuid.uuid4().hex);self.assertTrue(self.s.catalog_revision()['active'])

    def test_failed_tasks_have_separate_pages_and_can_be_removed_and_restored(self):
        for i in range(13):
            t=self.s.create('描述',uuid.uuid4().hex,title='失败'+str(i));t['status']='failed';self.s.save(t)
        page=self.s.catalog_page(limit=1);self.assertEqual(page['activity']['total'],13)
        self.assertEqual(len(page['activity']['ids']),10)
        next_page=self.s.catalog_page(task_offset=10);self.assertEqual(len(next_page['activity']['ids']),3)
        tid=next_page['activity']['ids'][0];self.s.library_update([tid],'trash_task')
        self.assertEqual(self.s.catalog_page(view='trash')['activity']['ids'],[tid])
        self.s.library_update([tid],'restore_task');self.assertEqual(self.s.catalog_page()['activity']['total'],13)

    def test_unknown_urls_never_exposed_and_validation(self):
        for body in ({'offset':True},{'limit':51},{'view':'unsafe'},{'search':1},{'sort':'DROP TABLE'}, {'include_ids':['bad']},{'task_offset':-1}):
            with self.assertRaises(ValueError):self.s.catalog_page(**body)
        for t in self.s.history()['tasks']:
            with self.s.connect() as c:stored=self.s._task(c,t['id'])
            stored['choices']=[{'url':'https://secret.example/file'}];self.s.save(stored)
        self.assertNotIn('secret.example',json.dumps(self.s.catalog_page()))
