"""Declared licence regression tests without font downloads or fontTools."""
import os
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             'skills', 'brand-identity', 'scripts'))
import font_audit


class TestDeclaredLicence(unittest.TestCase):
    def findings(self, declared, detected=None):
        target = {'kind': 'catalog', 'family': 'Test Family', 'source': 'fontshare',
                  'license': detected, 'weights': [], 'axes': {}}
        with mock.patch.object(font_audit, '_target', return_value=target), \
             mock.patch.object(font_audit.typelib, 'lang_id', return_value='en_Latn'):
            return {f['id']: f for f in font_audit.audit('Test Family', ['en'], license=declared)}

    def test_unknown_and_unrecognised_declarations_gate(self):
        for licence in ('unknown', 'unknwon', 'Definitely-Not-A-License'):
            with self.subTest(licence=licence):
                self.assertEqual(self.findings(licence)['type.license']['severity'], 'gate')

    def test_unrecognised_declaration_does_not_override_detected_licence(self):
        self.assertEqual(self.findings('unknwon', 'OFL-1.1')['type.license']['severity'], 'gate')

    def test_supported_free_aliases_remain_accepted(self):
        for licence in ('OFL', 'SIL OFL 1.1', 'Apache 2', 'UFL-1.0'):
            with self.subTest(licence=licence):
                self.assertNotIn('type.license', self.findings(licence))

    def test_commercial_and_fontshare_remain_notes(self):
        for licence in ('commercial', 'ITF-FFL-2.0'):
            with self.subTest(licence=licence):
                self.assertEqual(self.findings(licence)['type.license']['severity'], 'info')

    def test_missing_licence_still_gates(self):
        self.assertEqual(self.findings(None)['type.license']['severity'], 'gate')


if __name__ == '__main__':
    unittest.main()
