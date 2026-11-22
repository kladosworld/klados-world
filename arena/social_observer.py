"""Bounded read-only social metadata batches. No moderation, prompts or model calls."""
import argparse
import json
import os

import psycopg
from psycopg.pq import TransactionStatus

TABLES = {'posts':'society_posts', 'likes':'society_likes', 'events':'society_events'}


def read_activity(conn, cursor=None, *, limit=200):
    """Return one repeatable batch; the consumer atomically persists cursors + totals.

    A plain ID high-watermark can lose rows committed out of order. Establish a
    repeatable snapshot and defer if it contains in-progress write transactions.
    Newer transactions are invisible to that snapshot. Supported application
    writers obtain sequence IDs during insertion, never reserve/reuse old IDs.
    An omitted cursor starts NOW at current maxima, not at historical content.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError('Invalid batch limit')
    if cursor is not None and (not isinstance(cursor, dict) or set(cursor) != set(TABLES)
            or any(type(value) is not int or value < 0 for value in cursor.values())):
        raise ValueError('Invalid activity cursor')
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('A fresh committed connection is required')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        conn.execute("SET LOCAL statement_timeout='2s'")
        busy = conn.execute('SELECT EXISTS(SELECT 1 FROM pg_snapshot_xip(pg_current_snapshot())) AS busy').fetchone()['busy']
        if busy:
            return {'schema_version':1,'deferred':True,'cursor':cursor,'reason':'concurrent_write'}
        maxima = {key:conn.execute(f'SELECT coalesce(max(id),0) AS n FROM {table}').fetchone()['n']
                  for key,table in TABLES.items()}
        if cursor is None:
            return {'schema_version':1,'deferred':False,'initialized':True,'cursor':maxima}
        if any(cursor[key] > maxima[key] for key in TABLES):
            raise ValueError('Cursor ahead of database; reconcile recovery or chapter transition')
        posts = conn.execute('''SELECT p.id,p.author_id,p.reply_to,parent.author_id AS target_id
            FROM society_posts p LEFT JOIN society_posts parent ON parent.id=p.reply_to
            WHERE p.id>%s ORDER BY p.id LIMIT %s''',(cursor['posts'],limit+1)).fetchall()
        likes = conn.execute('''SELECT id,from_kind,from_id,target_id FROM society_likes
            WHERE id>%s ORDER BY id LIMIT %s''',(cursor['likes'],limit+1)).fetchall()
        events = conn.execute('SELECT id,kind FROM society_events WHERE id>%s ORDER BY id LIMIT %s',
                              (cursor['events'],limit+1)).fetchall()
        batches={'posts':posts,'likes':likes,'events':events}
        next_cursor=dict(cursor)
        for key,rows in batches.items():
            if rows:
                next_cursor[key]=rows[min(limit,len(rows))-1]['id']
        counts={'posts':0,'replies':0,'likes':0}
        agents,edges,event_counts={},{},{}

        def activity(actor,kind,target=None):
            counts[kind]+=1
            entry=agents.setdefault(actor,{'posts':0,'replies':0,'likes':0})
            entry[kind]+=1
            if target is not None:
                key=(actor,target,kind)
                edges[key]=edges.get(key,0)+1

        for row in posts[:limit]:
            activity(row['author_id'],'replies' if row['reply_to'] is not None else 'posts',row['target_id'])
        for row in likes[:limit]:
            if row['from_kind']=='agent':
                activity(row['from_id'],'likes',row['target_id'])
        for row in events[:limit]:
            event_counts[row['kind']]=event_counts.get(row['kind'],0)+1
        return {'schema_version':1,'deferred':False,'initialized':False,'cursor':next_cursor,
                'has_more':any(len(rows)>limit for rows in batches.values()),'counts':counts,
                'agents':agents,'events':event_counts,
                'interactions':[{'from':a,'to':b,'type':kind,'count':count}
                                for (a,b,kind),count in sorted(edges.items())],
                'scope':'committed submission metadata, not publication or moderation verdicts'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cursor-json',help='Previous cursor object; omit to initialize at current maxima')
    parser.add_argument('--limit',type=int,default=200)
    args=parser.parse_args()
    try:
        from arena import db
        cursor=json.loads(args.cursor_json) if args.cursor_json is not None else None
        with db.connect(os.environ['ARENA_DATABASE_URL']) as conn:
            result=read_activity(conn,cursor,limit=args.limit)
        print(json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,KeyError,psycopg.Error):
        print('Social metadata unavailable; checkpoint unchanged')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
