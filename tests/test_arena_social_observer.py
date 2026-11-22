"""Read-only incremental metadata contracts; no model requests or real society data."""
import json
import unittest
from unittest.mock import Mock

from arena.social_observer import read_activity
from tests.test_arena_external_agents import InviteCase


class CursorTests(unittest.TestCase):
    def test_invalid_cursor_or_limit_never_queries(self):
        conn=Mock()
        for cursor in ({}, {'posts':0,'likes':0,'events':True}, {'posts':-1,'likes':0,'events':0}):
            with self.subTest(cursor=cursor),self.assertRaises(ValueError):
                read_activity(conn,cursor)
        for limit in (0,501,True,'10'):
            with self.subTest(limit=limit),self.assertRaises(ValueError):
                read_activity(conn,limit=limit)
        conn.execute.assert_not_called()


class MetadataTests(InviteCase):
    def start(self):
        self.conn.commit()
        return read_activity(self.conn)['cursor']

    def post(self,conn,author='alder',parent=None):
        return conn.execute('''INSERT INTO society_posts(author_id,tick,text,reply_to,created_at)
            VALUES (%s,0,'PRIVATE_TEXT_CANARY',%s,%s) RETURNING id''',(author,parent,self.now)).fetchone()['id']

    def test_initialization_skips_history_and_batches_contain_no_private_content(self):
        self.post(self.conn)
        self.conn.commit()
        cursor=self.start()
        root=self.post(self.conn)
        self.post(self.conn,'birch',root)
        self.conn.execute("""INSERT INTO society_likes(from_kind,from_id,post_id,target_id,tick,day,paid,created_at)
            VALUES ('agent','cedar',%s,'alder',0,0,false,%s)""",(root,self.now))
        self.conn.commit()
        result=read_activity(self.conn,cursor)
        self.assertFalse(result['deferred'])
        self.assertEqual(result['counts'],{'posts':1,'replies':1,'likes':1})
        self.assertNotIn('PRIVATE_TEXT_CANARY',json.dumps(result))
        self.assertEqual(len(result['interactions']),2)
        replay=read_activity(self.conn,result['cursor'])
        self.assertEqual(sum(replay['counts'].values()),0)

    def test_batches_are_bounded_and_do_not_repeat_counted_ids(self):
        cursor=self.start()
        for _ in range(5):self.post(self.conn)
        self.conn.commit()
        total=0
        for expected in (2,2,1):
            result=read_activity(self.conn,cursor,limit=2)
            self.assertEqual(result['counts']['posts'],expected)
            total+=expected;cursor=result['cursor']
        self.assertEqual(total,5)
        self.assertFalse(result['has_more'])

    def test_out_of_order_commit_defers_checkpoint_until_rows_are_visible(self):
        cursor=self.start()
        with self.connection() as first:
            low=self.post(first)
            with self.connection() as second:
                high=self.post(second,'birch')
            self.assertLess(low,high)
            result=read_activity(self.conn,cursor)
            self.assertTrue(result['deferred'])
            self.assertEqual(result['cursor'],cursor)
        result=read_activity(self.conn,cursor)
        self.assertFalse(result['deferred'])
        self.assertEqual(result['counts']['posts'],2)
        self.assertEqual(result['cursor']['posts'],high)

    def test_recovery_cursor_ahead_of_database_refuses(self):
        cursor=self.start();cursor['posts']+=100
        with self.assertRaisesRegex(ValueError,'reconcile recovery'):
            read_activity(self.conn,cursor)
