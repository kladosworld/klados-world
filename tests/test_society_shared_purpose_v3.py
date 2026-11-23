"""Additional reviewed-purpose compatibility without publishing private build inputs."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from tee_arena.society import constitution, prompts, shared_purpose as purpose


class AdditionalPurposeTests(unittest.TestCase):
    def test_existing_versions_and_constitution_are_unchanged(self):
        self.assertEqual(purpose.LAUNCH_VERSION, 'building-a-society-v2')
        self.assertEqual(purpose.LAUNCH_LAYER_SHA256, '4eab13cdf160e3d451c1cfbf1017a44681e127ef6401f8e5f76f6efe38ffd3bd')
        self.assertEqual(purpose.APPROVED_LAYER_SHA256, 'e7d80cd0b482f19422d3c20ca0b96691fb6d322b7d17001aa7691c569135548f')
        self.assertEqual(constitution.VERSION, 6)
        self.assertEqual(constitution.fingerprint(), 'c3c6984c305f31320cf49b50d928b49c277a6aa55b47820c900258614034ad4e')
        self.assertEqual(purpose.APPROVED_PURPOSES[purpose.EVOLUTION_VERSION],
                         (purpose.EVOLUTION_TEXT_SHA256,purpose.EVOLUTION_LAYER_SHA256))

    def test_selection_is_explicit_and_missing_build_input_fails_closed(self):
        with patch.object(purpose,'load_registry',side_effect=AssertionError('not selected')):
            self.assertIsNone(purpose.from_environ({}))
            self.assertIsNone(purpose.from_environ({'KLADOS_SHARED_PURPOSE':'off'}))
        with patch.object(purpose,'load_registry',return_value={}):
            with self.assertRaisesRegex(purpose.PurposeConfigurationError,'purpose_version_unavailable'):
                purpose.from_environ({'KLADOS_SHARED_PURPOSE':purpose.EVOLUTION_VERSION})

    def test_unapproved_text_cannot_use_the_reviewed_version(self):
        selected=purpose.Purpose(purpose.EVOLUTION_VERSION,'Synthetic test text.')
        with self.assertRaisesRegex(purpose.PurposeConfigurationError,'unapproved_purpose_registry'):
            purpose.validate_reviewed_registry({selected.version:selected})

    def test_turn_and_reflection_preserve_identity_and_memory(self):
        selected=purpose.Purpose('synthetic-evolution-v1','Synthetic optional motivation.')
        state={'state_version':2,'you':{'personality':'Original tendencies','private_notes':['PRIVATE_MEMORY']}}
        before=deepcopy(state)
        for compose in (prompts.turn,prompts.reflection):
            original,messages=compose(state)
            system,enabled_messages=compose(state,purpose=selected)
            self.assertEqual(system,selected.prefix()+original)
            self.assertEqual(messages,enabled_messages)
            self.assertIn(constitution.TEXT,system)
            self.assertIn('PRIVATE_MEMORY',messages[0]['content'])
        self.assertEqual(state,before)
