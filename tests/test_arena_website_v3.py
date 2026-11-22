"""Local gallery safety and public conversation metadata, using synthetic data only."""
from pathlib import Path
import unittest
from fastapi.testclient import TestClient

from arena import avatars, society, society_public
from arena.app import create_app
from tests.test_arena_world_feed import WorldFeedCase


class GalleryTests(unittest.TestCase):
    def test_only_small_locally_authored_images_are_selectable(self):
        folder = Path(__file__).resolve().parents[1] / 'arena/static/avatars'
        for key in avatars.CHOICES | avatars.FOUNDERS:
            raw = (folder / (key+'.svg')).read_bytes()
            self.assertLess(len(raw), 8192)
            self.assertTrue(raw.startswith(b'<svg '))
            for forbidden in (b'<script', b'<image', b'foreignObject', b'href=', b'onload=', b'data:'):
                self.assertNotIn(forbidden, raw)
        for value in ('https://example.com/a.png','../alder','data:image/png;base64,x','<svg>',None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                avatars.validate(value)
        self.assertEqual(avatars.public_key({'id':'custom','kind':'external'}),'orbit')
        self.assertEqual(len({avatars.public_key({'id':key,'kind':'founder'}) for key in avatars.FOUNDERS}),4)

    def test_gallery_is_served_as_image_without_authentication(self):
        with TestClient(create_app()) as client:
            response=client.get('/static/avatars/alder.svg')
        self.assertEqual(response.status_code,200)
        self.assertIn('image/svg+xml',response.headers['content-type'])


class ConversationTests(WorldFeedCase):
    def test_old_root_is_included_and_counts_exclude_unreviewed_replies(self):
        self.tick()
        root=self.founder_post('A synthetic root contribution.')
        self.tick()
        reply=self.founder_post('A synthetic public response.',reply_to=root)
        agent=self.register().json()
        self.client.post('/api/agents/me/actions',headers=self.headers(agent),json={
            'action':'reply','post_id':root,'text':'Pending private-to-moderation content.'})
        posts=society_public.public_posts(self.conn,society.clock(self.conn)['tick'],limit=1)
        self.assertEqual({post['id'] for post in posts},{root,reply})
        self.assertTrue(all(post['root_id']==root and post['reply_count']==1 for post in posts))
        self.assertNotIn('Pending private-to-moderation',str(posts))

    def test_avatar_selection_is_authenticated_and_never_accepts_remote_urls(self):
        agent=self.register().json()
        self.assertEqual(self.client.patch('/api/agents/me',json={'avatar':'prism'}).status_code,401)
        for value,status in (('prism',200),('https://example.com/x.png',400),('../alder',400)):
            self.assertEqual(self.client.patch('/api/agents/me',headers=self.headers(agent),json={'avatar':value}).status_code,status)
        self.assertEqual(self.client.get('/api/society/agents/'+agent['id']).json()['avatar'],'prism')

    def test_requested_conversation_is_revalidated_after_moderation_change(self):
        self.tick()
        root=self.founder_post('Synthetic retained conversation.')
        self.tick()
        self.founder_post('Synthetic newer conversation.')
        posts=society_public.public_posts(self.conn,society.clock(self.conn)['tick'],limit=1,opened=(root,))
        self.assertIn(root,{post['id'] for post in posts})
        self.conn.execute("UPDATE society_posts SET status='withheld' WHERE id=%s",(root,))
        self.conn.commit()
        posts=society_public.public_posts(self.conn,society.clock(self.conn)['tick'],limit=1,opened=(root,))
        self.assertNotIn(root,{post['id'] for post in posts})
