"""Reviewed-version selection contracts; real prompt wording remains a private build input."""
from copy import deepcopy
from hashlib import sha256
import unittest
from unittest.mock import patch

from tee_arena.society import shared_purpose as purpose


class DayOnePurposeTests(unittest.TestCase):
    def test_new_launch_pins_preserve_existing_reviewed_version(self):
        self.assertEqual(purpose.LAUNCH_VERSION, 'building-a-society-v2')
        self.assertEqual(purpose.LAUNCH_TEXT_SHA256, 'c3ebd436bf6c3b5480fe67fa5201365ea800c2ae2364164034323ad933e3feee')
        self.assertEqual(purpose.LAUNCH_LAYER_SHA256, '4eab13cdf160e3d451c1cfbf1017a44681e127ef6401f8e5f76f6efe38ffd3bd')
        self.assertEqual(purpose.APPROVED_PURPOSES['building-a-society-v1'],
                         ('871041773852a433d19ba6d55afa313bf5bb377b7cedc327a45d329c6fd20065',
                          'e7d80cd0b482f19422d3c20ca0b96691fb6d322b7d17001aa7691c569135548f'))
        self.assertEqual(purpose.APPROVED_PURPOSES[purpose.LAUNCH_VERSION],
                         (purpose.LAUNCH_TEXT_SHA256, purpose.LAUNCH_LAYER_SHA256))
        self.assertIsNone(purpose.from_environ({}))
        with self.assertRaisesRegex(purpose.PurposeConfigurationError, '^purpose_version_unavailable$'):
            purpose.from_environ({purpose.VARIABLE: purpose.LAUNCH_VERSION})

    def test_build_registry_accepts_only_exact_approved_version_content_pairs(self):
        fixtures = {v: purpose.Purpose(v, 'Synthetic reviewed fixture ' + v)
                    for v in (purpose.APPROVED_VERSION, purpose.LAUNCH_VERSION)}
        pins = {v: (sha256(item.text.encode()).hexdigest(), item.fingerprint())
                for v, item in fixtures.items()}
        with patch.object(purpose, 'APPROVED_PURPOSES', pins):
            for versions in ((purpose.APPROVED_VERSION,), (purpose.LAUNCH_VERSION,), tuple(fixtures)):
                registry = {v: fixtures[v] for v in versions}
                before = deepcopy(registry)
                self.assertEqual(purpose.validate_reviewed_registry(registry), before)
                with patch.object(purpose, 'load_registry', return_value=registry):
                    for v in versions:
                        self.assertEqual(purpose.from_environ({purpose.VARIABLE: v}), fixtures[v])
                self.assertEqual(registry, before)
            invalid = ({}, {'unknown-v1': fixtures[purpose.LAUNCH_VERSION]},
                       {purpose.LAUNCH_VERSION: fixtures[purpose.APPROVED_VERSION]},
                       {purpose.LAUNCH_VERSION: purpose.Purpose(purpose.LAUNCH_VERSION, 'Tampered synthetic fixture')})
            for registry in invalid:
                with self.subTest(keys=list(registry)), self.assertRaisesRegex(
                        purpose.PurposeConfigurationError, '^unapproved_purpose_registry$'):
                    purpose.validate_reviewed_registry(registry)
