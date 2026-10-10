"""Regression tests requiring no font, browser or asset fixtures."""
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/brand-identity/scripts'))
import export_tokens as et


class CommentLabels(unittest.TestCase):
    def test_plain_label_is_preserved(self):
        self.assertEqual(et.comment_label('Harbor Blue'), 'Harbor Blue')

    def test_terminator_and_line_separators(self):
        self.assertEqual(et.comment_label('Blue */\r\nred\u2028green\u2029yellow'),
                         'Blue * / red green yellow')

    def test_generated_comments_contain_names(self):
        label = 'Blue */\nreview-marker;\n/*'
        pal = {'name': label, 'brand': [{'id': 'brand-1', 'hex': '#123456', 'name': label}],
               'extended': [{'id': 'ext-1', 'hex': '#abcdef', 'on': '#000000', 'name': label}],
               'modes': {}}
        for writer in (et.to_css, et.to_scss, et.to_tailwind):
            with self.subTest(writer=writer.__name__):
                result = writer(pal)
                self.assertNotIn('\nreview-marker;', result)
                self.assertIn('Blue * / review-marker; /*', result)

    def test_gpl_names_stay_on_one_line(self):
        label = 'Blue\r\n12 34 56\tfake\u2028row'
        pal = {'name': label, 'brand': [{'id': 'brand-1', 'hex': '#123456', 'name': label}],
               'extended': [{'id': 'ext-1', 'hex': '#abcdef', 'on': '#000000', 'name': label}],
               'modes': {}}
        lines = et.to_gpl(pal).splitlines()
        self.assertIn('Name: Blue 12 34 56\tfake row', lines)
        self.assertNotIn('12 34 56\tfake', [l for l in lines if l.startswith('12 34 56')])
        rows = [l for l in lines[4:] if l.strip()]
        self.assertTrue(all(l[:11].replace(' ', '').isdigit() for l in rows), rows)


if __name__ == '__main__':
    unittest.main()
