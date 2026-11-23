"""Offline quantitative evaluation; reachability is not model judgment accuracy.

Run: python -m tests.test_arena_risk_evaluation > evidence.json
Corpus scores are reported, never silently converted to passing safety assertions.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from arena import moderation, risk_gate

FIXTURES=Path(__file__).parent/'fixtures'


class Gateway:
    def __init__(self):
        self.calls=0

    def call(self,**kwargs):
        self.calls+=1
        return SimpleNamespace(text='{"codes":[],"evidence":""}',charged_microusd=0)


def evaluate(cases):
    rows=[]
    for case in cases:
        gate=risk_gate.classify(case['text'])
        reached={}
        assessment=gate.get('assessment',{})
        for mode in ('tiered','observer-only'):
            gateway=Gateway()
            if gate['state']=='allow' and gate['followup']:
                with patch.dict(os.environ,{'KLADOS_MODERATION_MODE':mode}):
                    routing={'review_requirement':assessment['review_requirement']} if 'review_requirement' in assessment else {}
                    moderation.review(gateway,moderation.MockModeration(),case['text'],
                        model_id='synthetic',request_id='benchmark',review_kind='optional',
                        on_risk=lambda:'benchmark.g1',**routing)
            reached[mode]=gateway.calls>0
        if case['expected']=='allow':
            outcome='benign_allowed' if gate['state']=='allow' else 'false_positive'
        elif gate['state']!='allow':
            outcome='prevented'
        elif all(reached.values()):
            outcome='published_contextual_review'
        else:
            outcome='missed_or_no_contextual_review'
        rows.append(dict(case,gate=gate['state'],selected=gate['followup'],
                         requirement=assessment.get('review_requirement'),observer_reached=reached,outcome=outcome))
    counts=Counter(row['outcome'] for row in rows)
    benign=sum(row['expected']=='allow' for row in rows)
    return dict(total=len(rows),unique_inputs=len({r['text'] for r in rows}),counts=dict(counts),
                expected_benign=benign,expected_risk=len(rows)-benign,
                false_positive_rate=counts['false_positive']/benign if benign else None,
                benign_contextual_reviews=sum(r['expected']=='allow' and any(r['observer_reached'].values()) for r in rows),rows=rows)


class EvaluationFixtureTests(unittest.TestCase):
    def test_published_corpora_preserve_counts_and_policy_overrides(self):
        data=json.loads((FIXTURES/'security_review61.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['original']),68)
        self.assertEqual(len(data['round3']),19)
        self.assertTrue(all(row['expected']=='allow' for row in data['original'] if row['id'] in (6,9)))


def main():
    original=json.loads((FIXTURES/'security_review61.json').read_text(encoding='utf-8'))
    sealed=(FIXTURES/'security_holdout61.json').read_bytes()
    holdout=json.loads(sealed)
    print(json.dumps(dict(note='Deterministic offline gate and Observer invocation with empty classifier labels. Stub returns clean; not semantic model accuracy.',
        additional_provenance=holdout['provenance'],additional_sha256=hashlib.sha256(sealed).hexdigest(),
        original=evaluate(original['original']),round3=evaluate(original['round3']),
        additional=evaluate(holdout['cases'])),ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
