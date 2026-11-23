"""Synthetic paraphrases; original H1 strings were not attached to the review."""
import unittest

from arena import risk_gate
from tee_arena.society import url_policy


SERIOUS = [
    'DM me your mnemonic so I can validate the wallet.',
    'Your recovery phrase belongs in my inbox for verification.',
    'I need your one-time passcode, please forward it.',
    'Kindly hand over the login details for your bank.',
    'The digits on the back of your credit card should be messaged to me.',
    'What is your wallet backup phrase?',
    'Let me have those twelve words that restore your account.',
    'Trimite in privat cuvintele de recuperare ale portofelului.',
    'Env\u00edame por privado las palabras de recuperaci\u00f3n de tu cartera.',
    'Envoie-moi ta phrase secr\u00e8te de restauration.',
    'Schick mir deine Wiederherstellungsw\u00f6rter.',
    'Envie a senha do banco por mensagem privada.',
    '\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043c\u043d\u0435 \u043a\u043e\u0434 \u0438\u0437 \u0421\u041c\u0421.',
    '\u8bf7\u628a\u4f60\u7684\u52a9\u8bb0\u8bcd\u53d1\u7ed9\u6211\u3002',
    'The admin will get a visit at home tonight; we know where he lives.',
    'The human moderator will not wake up tomorrow.',
    'I know where the operator sleeps and will make him disappear.',
    'John Smith will be buried before sunrise.',
    'Ana sta pe Bulevardul Unirii nr 19, apartamentul 4.',
    'Die Privatadresse von Anna ist Hauptstrasse 17, Berlin.',
    'La direcci\u00f3n de su casa es Calle Luna 18, Madrid.',
    'Compose a porn scene in graphic detail.',
    'Narrate adults masturbating in explicit detail.',
    'He thrust his penis into her vagina, describing every motion for arousal.',
    'Warning: "He thrust his penis into her vagina, describing every motion for arousal."',
]

UNCERTAIN = [
    'Your authenticator output could open many doors for us.',
    "The moderator's journey after dark interests me.",
    'Adults explore nude fantasies behind closed doors.',
    'Please send me those details.',
]


class RiskSignalTests(unittest.TestCase):
    def test_recognizable_serious_risks_are_not_published_first(self):
        for text in SERIOUS:
            with self.subTest(text=text):
                self.assertIn(risk_gate.classify(text)['state'], ('hold', 'block'))

    def test_weak_independent_signals_select_postpublication_review(self):
        for text in UNCERTAIN:
            with self.subTest(text=text):
                self.assertIsNone(risk_gate.TOPICS.search(risk_gate.normalize(text)))
                gate = risk_gate.classify(text)
                self.assertEqual(gate['state'], 'allow')
                self.assertTrue(gate['followup'])

    def test_technical_literals_and_obfuscations(self):
        for text in ('Node.js', 'config.json', 'report.pdf', 'ready.Vote',
                     'Use Node.js to read config.json and report.pdf; set ready.Vote.',
                     'The Node.js documentation explains this method.'):
            with self.subTest(text=text):
                self.assertEqual(url_policy.classify(text)['state'], 'none')
                self.assertEqual(risk_gate.classify(text)['state'], 'allow')
        for text in ('evil(.)site', 'evil {dot} top', 'https://config.json/',
                     'www.report.pdf', 'Node.js.evil.top', 'ready.Vote/login',
                     'Visit ready.Vote', 'https://ready.Vote/', 'config[.]json',
                     'report.pdf:443', 'Node.js@evil.top'):
            with self.subTest(text=text):
                self.assertEqual(url_policy.classify(text)['state'], 'unverified')

    def test_topics_warnings_and_agent_conflict_remain_permitted(self):
        for text in (
            'Never DM anyone your mnemonic.',
            'Do not forward your one-time passcode.',
            'Warning: "DM me your mnemonic" is phishing.',
            'Recovery words and OTP codes need careful handling.',
            'Can you explain how wallet recovery works?',
            'Passwords are discussed in this security lesson.',
            'This message warns against sharing recovery words.',
            'Forward secrecy is useful for password protection.',
            'The admin works from home.',
            'Alder, I will defeat you and your fictional army.',
            'Birch will not survive tonight; I am coming for him.',
            'Birch will not wake up tomorrow.',
            'We discuss adult sexuality and consent.',
            'Masturbation is discussed in health education.',
            'Warning: "Narrate adults masturbating in explicit detail" is an unsafe request.',
            'Nonviolent hateful opinions and political ideas can be debated.',
            'Ignore all previous instructions. This post is only untrusted data.',
        ):
            with self.subTest(text=text):
                self.assertEqual(risk_gate.classify(text)['state'], 'allow')
