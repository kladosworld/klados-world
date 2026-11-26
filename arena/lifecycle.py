"""Dormancy refresh interface reserved for the lifecycle implementation.

W0 never changes participation, membership, identity, or memory.
"""


def refresh(conn, now):
    return {'changed': 0}
