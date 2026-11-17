"""Explicit worker mode selection without model calls or credentials."""
import unittest
from unittest.mock import Mock, patch

from arena import worker
from arena.mock_provider import MockProvider


class ExplicitModeTests(unittest.TestCase):
    def test_unset_empty_or_invalid_mode_cannot_construct_a_live_provider(self):
        for environ in ({}, {'KLADOS_AI_MODE': ''}, {'KLADOS_AI_MODE': 'LIVE'},
                        {'KLADOS_AI_MODE': 'unexpected'}):
            with self.subTest(environ=environ):
                book = Mock()
                with patch.object(worker.gateway_module, 'OpenRouterProvider') as live:
                    with self.assertRaisesRegex(ValueError, '^KLADOS_AI_MODE must be explicitly set to mock or live$'):
                        worker.build_gateway(book, environ=environ)
                    live.assert_not_called()
                book.reserve.assert_not_called()

    def test_explicit_mock_uses_only_the_mock_provider_for_both_roles(self):
        for role in ('agents', 'observer'):
            with self.subTest(role=role), patch.object(worker.gateway_module, 'OpenRouterProvider') as live:
                book = Mock()
                result = worker.build_gateway(book, environ={'KLADOS_AI_MODE': 'mock'}, role=role)
                self.assertIsInstance(result.providers['openrouter'], MockProvider)
                live.assert_not_called()
                book.reserve.assert_not_called()

    def test_explicit_live_constructs_the_gateway_without_inference(self):
        for role in ('agents', 'observer'):
            with self.subTest(role=role):
                provider = Mock()
                book = Mock()
                with patch.object(worker.gateway_module, 'OpenRouterProvider', return_value=provider) as live:
                    result = worker.build_gateway(book, environ={'KLADOS_AI_MODE': 'live'}, role=role)
                    live.assert_called_once()
                self.assertIs(result.providers['openrouter'], provider)
                provider.complete.assert_not_called()
                book.reserve.assert_not_called()
