"""Small approved local gallery; never accept image uploads, user SVG or remote URLs."""
FOUNDERS = frozenset(('alder', 'birch', 'cedar', 'hazel'))
CHOICES = frozenset(('orbit', 'prism', 'wave', 'sprout', 'spark', 'grid'))


def public_key(agent):
    if agent['kind'] == 'founder' and agent['id'] in FOUNDERS:
        return agent['id']
    key = agent.get('avatar_key')
    return key if key in CHOICES else 'orbit'


def validate(key):
    if not isinstance(key, str) or key not in CHOICES:
        raise ValueError('Invalid avatar choice')
    return key
