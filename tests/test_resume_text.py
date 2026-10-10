import unittest
from pathlib import Path

from backend.resume_text import TextDocument, text_document


class ResumeTextTests(unittest.TestCase):
    def doc(self, text):
        return TextDocument(r'\documentclass{article}\newcommand{\secret}{style}\begin{document}' + text + r'\end{document}')

    def test_system_templates_round_trip_hide_preamble_photo_and_layout(self):
        for path in (Path(__file__).resolve().parents[1] / '模板').glob('*.tex'):
            source = path.read_text()
            doc = TextDocument(source)
            self.assertFalse(doc.partial, path.name)
            self.assertGreater(len(doc.fields), 20)
            self.assertEqual(doc.edit({f['id']: f['text'] for f in doc.fields}), source)
            text = '\n'.join(f['text'] for f in doc.fields)
            for hidden in ('photo.jpg', 'headercolor', 'minipage', '0.78', 'itemsep', 'vspace', 'textbf'):
                self.assertNotIn(hidden, text)

    def test_edit_repeated_text_only_in_selected_paragraph_and_keep_nested_styles(self):
        doc = self.doc(r'\item Built \textbf{fast \emph{APIs}} with Python.\item Built APIs with Python.')
        changed = doc.edit({'0': 'Built reliable fast APIs with Go.'})
        self.assertIn(r'\textbf{', changed)
        self.assertIn(r'\emph{APIs}', changed)
        self.assertIn(r'\item Built APIs with Python.', changed)
        self.assertEqual(TextDocument(changed).fields[0]['text'], 'Built reliable fast APIs with Go.')
        self.assertEqual(changed.split(r'\begin{document}')[0], doc.source.split(r'\begin{document}')[0])

    def test_literal_symbols_cannot_become_commands_or_comments(self):
        doc = self.doc(r'\item \textbf{Backend} development')
        value = r'Backend & C# cost 5% {data} $x _key ~ ^ \input{secret}'
        changed = doc.edit({'0': value})
        self.assertEqual(TextDocument(changed).fields[0]['text'], value)
        self.assertNotIn(r'\input{secret}', changed)
        self.assertIn(r'\&', changed)
        self.assertIn(r'\%', changed)
        self.assertIn(r'\textbf{Backend}', changed)

    def test_email_and_matching_link_target_follow_visible_text(self):
        doc = self.doc(r'\href{mailto:a@example.com}{a@example.com}\par\href{https://example.com/me}{example.com/me}\par\href{https://example.com}{Portfolio}')
        changed = doc.edit({'0': 'b@example.com', '1': 'example.com/new', '2': 'My work'})
        self.assertIn(r'\href{mailto:b@example.com}{b@example.com}', changed)
        self.assertIn(r'\href{https://example.com/new}{example.com/new}', changed)
        self.assertIn(r'\href{https://example.com}{My work}', changed)

    def test_unknown_macros_math_and_comments_untouched(self):
        doc = self.doc('\\item Visible % hidden comment\n\\custom{layout-data}{secret text}\\par $x^2$\\par\\item Other')
        self.assertTrue(doc.partial)
        self.assertEqual([f['text'] for f in doc.fields], ['Visible', 'Other'])
        changed = doc.edit({'0': 'Changed'})
        self.assertIn('% hidden comment', changed)
        self.assertIn(r'\custom{layout-data}{secret text}', changed)
        self.assertIn('$x^2$', changed)

    def test_stale_fields_invalid_values_and_atomic_macros(self):
        doc = self.doc(r'\item \LaTeX{} editing')
        for edits in ({'missing': 'text'}, {'0': None}, {'0': 'x'*10001}, {'0': 'LaTeY editing'}):
            with self.assertRaises(ValueError):
                doc.edit(edits)
        self.assertEqual(TextDocument(doc.edit({'0': '工具 editing'})).fields[0]['text'], '工具 editing')

    def test_chinese_emoji_empty_and_multiline(self):
        doc = self.doc(r'\item 完成\textbf{接口开发}，提升效率。\item 第二点')
        value = '完成接口开发，提升效率 & 稳定性🙂。'
        changed = doc.edit({'0': value, '1': ''})
        self.assertEqual([f['text'] for f in TextDocument(changed).fields], [value])
        changed = TextDocument(changed).edit({'0': value+'\n补充结果。'})
        self.assertEqual(TextDocument(changed).fields[0]['text'], value+' 补充结果。')
        self.assertTrue(text_document('invalid source')['partial'])

    def test_selected_bold_toggle_preserves_text_and_nested_styles(self):
        doc = self.doc(r'\item Build \emph{APIs} with \textbf{Python}.\item Other')
        content = doc.format_selection('0', 6, 10, True)
        self.assertIn(r'\emph{\textbf{APIs}}', content)
        parsed = TextDocument(content)
        self.assertEqual(parsed.public()['fields'][0]['bold'], [[6, 10], [16, 22]])
        content = parsed.format_selection('0', 6, 22, False)
        parsed = TextDocument(content)
        self.assertEqual(parsed.public()['fields'][0]['bold'], [])
        self.assertEqual(parsed.fields[0]['text'], doc.fields[0]['text'])
        self.assertIn(r'\item Other', content)
        self.assertEqual(content.split(r'\begin{document}')[0], doc.source.split(r'\begin{document}')[0])

    def test_format_after_pending_edits_and_cleared_earlier_field(self):
        doc = self.doc(r'\item First\item Work on APIs')
        result = text_document(doc.source, {'0': '', '1': 'Work on C# & APIs'},
                               {'field_id': '1', 'start': 8, 'end': 10, 'bold': True})
        self.assertEqual(result['selection']['field_id'], '0')
        self.assertEqual(result['fields'][0]['text'], 'Work on C# & APIs')
        self.assertEqual(result['fields'][0]['bold'], [[8, 10]])
        self.assertIn(r'\textbf{C\#}', result['content'])

    def test_format_unicode_and_atomic_command_and_validation(self):
        doc = self.doc(r'\item 用🙂与\LaTeX{}排版')
        revised = doc.format_selection('0', 1, 8, True)
        self.assertEqual(TextDocument(revised).public()['fields'][0]['bold'], [[1, 8]])
        self.assertEqual(TextDocument(revised).fields[0]['text'], doc.fields[0]['text'])
        for start,end,bold in [(-1,2,True),(1,1,True),(0,500,True),(True,2,True),(0,2,'yes'),(4,5,True)]:
            with self.assertRaises(ValueError):doc.format_selection('0',start,end,bold)


if __name__ == '__main__':
    unittest.main()
