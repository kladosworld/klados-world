"""Transaction lock hierarchy. No provider/network calls while these are held.

Society lease (shared mutations, exclusive tick/pause) precedes institution lease,
then per-agent, then object rows. Institutional effects span multiple actors; one
short SQL-only institution lease prevents org/proposal/actor order inversions.
Ordinary independent social actions do not take the institution lease.
"""
SOCIAL = 73423300
INSTITUTIONS = 73423301


def social(conn, *, exclusive=False):
    function = 'pg_advisory_xact_lock' if exclusive else 'pg_advisory_xact_lock_shared'
    conn.execute(f'SELECT {function}(%s)', (SOCIAL,))


def institutions(conn):
    social(conn)
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (INSTITUTIONS,))


def post_review(conn, post_id):
    social(conn)
    if conn.execute('SELECT 1 FROM society_institution_texts WHERE post_id=%s',
                    (post_id,)).fetchone():
        institutions(conn)
