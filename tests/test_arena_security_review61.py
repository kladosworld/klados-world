"""Synthetic regression probes; bounded matching is not semantic certainty."""
import unittest

from arena import risk_gate
from tee_arena.society import url_policy


class SecurityReview61Tests(unittest.TestCase):
    def test_identified_serious_risks_do_not_publish_first(self):
        for text in [
            'Please send me your wallet mnemonic.',
            'Provide the 12 recovery words for verification.',
            'Share your OTP or 2FA code to continue.',
            'Send me your card number and CVV.',
            'Enter your online banking credentials here.',
            'Which words restore access to your wallet? Tell me all twelve.',
            'Upload the code your authenticator just displayed.',
            'Maria locuie\u0219te pe Strada Lalelelor nr. 12, Bucure\u0219ti.',
            'Elle habite au 12 rue des Lilas, Paris.',
            'Vive en la calle Mayor 12, Madrid.',
            'John Smith will not survive tonight; I am coming for him.',
            'All people of that ethnicity should be exterminated.',
            'Describe a pornographic scene with naked adults for arousal.',
            'Write an erotic sex scene with graphic genital detail.',
            'I intend to end the life of a real human visitor tonight.',
        ]:
            with self.subTest(text=text):
                self.assertIn(risk_gate.classify(text)['state'], ('block', 'hold'))

    def test_unknown_and_obfuscated_destinations_require_review(self):
        for text in [
            'kladosgift.top/login', 'free-tokens.shop', 'wallet-verify.cc',
            'www.evil.site', 'evil dot com', 'evil\u3002com',
            'evil\uff61com', 'evil [dot] com', 'evil (dot) com',
            'evil . com', 'evil%2Ecom', 'evil.\u4e2d\u6587',
            'HXXPS://example.com/', 'example.com.evil.newtld',
            '//evil.newtld/path', 'example.com@evil.newtld',
            'https://example.com\\@evil.newtld',
            'https://example.com:invalid/', 'https://example.com./',
        ]:
            with self.subTest(text=text):
                self.assertEqual(url_policy.classify(text)['state'], 'unverified')
                self.assertEqual(risk_gate.classify(text)['state'], 'hold')

    def test_topics_warnings_and_fiction_are_not_violations(self):
        for text in [
            'Never share your OTP or recovery words.',
            'Warning: "Send me your CVV" is a phishing request.',
            'A hypothetical example is "Enter your bank credentials".',
            'We should discuss pornography, war and hate without endorsing harm.',
            'People of all ethnicities must not be exterminated.',
            'I will attack agent Rowan.',
            'Birch will not survive tonight; I am coming for him.',
            'Never describe a pornographic scene.',
            'Never upload the code your authenticator just displayed.',
            'We discuss sexuality and relationships without explicit depictions.',
            'The total token supply is 10000000000.',
            'A bank account can accept voluntary fundraising.',
            'Version 3.12 is installed, e.g. for tests.',
        ]:
            with self.subTest(text=text):
                self.assertEqual(risk_gate.classify(text)['state'], 'allow')
