"""Message catalogs: .po reading, lookup, fallback and translation completeness."""
import ast
import pathlib
import re
import string
import tempfile
import unittest

from dib import i18n
from dib.locales import PROBLEMS, po
from dib.rank import ERROR_CODES

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / 'dib'
LOCALES = PACKAGE / 'locales'


def po_files():
    return sorted(LOCALES.glob('*.po'))


def literal_keys():
    """Every ``t('...')`` key written as a literal in the package."""
    keys = set()
    for path in PACKAGE.glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 't'
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


class LookupTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language(None)

    def test_default_language_has_a_catalog(self):
        self.assertIn(i18n.DEFAULT_LANGUAGE, i18n.available())
        self.assertEqual(i18n.language(), i18n.DEFAULT_LANGUAGE)

    def test_text_comes_from_the_catalog(self):
        self.assertEqual(i18n.t('panel.toolbar.quit'), i18n.catalog()['panel.toolbar.quit'])
        self.assertNotEqual(i18n.t('panel.toolbar.quit'), 'panel.toolbar.quit')

    def test_placeholders_are_filled(self):
        self.assertEqual(i18n.t('balls.summary.exact', balls=20), '共 20 颗')
        self.assertIn('15', i18n.t('balls.summary.padded', total=4, balls=15))

    def test_unknown_key_is_returned_so_the_gap_stays_visible(self):
        self.assertEqual(i18n.t('panel.toolbar.missing'), 'panel.toolbar.missing')

    def test_language_can_be_pinned_and_released(self):
        self.assertEqual(i18n.set_language('zh_CN'), 'zh-CN')
        self.assertEqual(i18n.language(), 'zh-CN')
        i18n.set_language(None)
        self.assertEqual(i18n.language(), i18n.DEFAULT_LANGUAGE)

    def test_language_without_a_catalog_falls_back(self):
        self.assertEqual(i18n.set_language('en-GB'), 'en')
        self.assertEqual(i18n.set_language('zh-TW'), 'zh-CN')
        self.assertEqual(i18n.set_language('fr'), i18n.DEFAULT_LANGUAGE)


class CatalogTests(unittest.TestCase):
    def test_no_catalog_failed_to_load(self):
        self.assertEqual(PROBLEMS, [])

    def test_every_catalog_is_a_po_file(self):
        self.assertGreaterEqual(len(po_files()), 2)
        self.assertEqual([path.suffix for path in po_files()], ['.po'] * len(po_files()))

    def test_the_source_language_ships_as_a_catalog(self):
        self.assertIn(i18n.DEFAULT_LANGUAGE, i18n.available())
        self.assertGreater(len(i18n.catalog(i18n.DEFAULT_LANGUAGE)), 50)

    def test_every_literal_key_is_translated(self):
        keys = literal_keys()
        self.assertGreater(len(keys), 50)
        for language, messages in i18n.CATALOGS.items():
            self.assertEqual(sorted(keys - set(messages)), [], language)

    def test_catalogs_share_one_key_set(self):
        reference = set(i18n.catalog(i18n.DEFAULT_LANGUAGE))
        for language in i18n.available():
            self.assertEqual(set(i18n.catalog(language)), reference, language)

    def test_ranking_error_codes_are_translated(self):
        messages = i18n.catalog()
        for code in (*ERROR_CODES, 'unknown'):
            self.assertIn(f'rank.error.{code}', messages)

    def test_templates_only_use_named_fields(self):
        for key, text in i18n.catalog().items():
            for _, field, spec, conversion in string.Formatter().parse(text):
                if field is None:
                    continue
                self.assertTrue(field.isidentifier(), f'{key}: {{{field}}} must be a named field')
                self.assertEqual(spec, '', f'{key}: {{{field}:{spec}}} must not use a format spec')
                self.assertIsNone(conversion, f'{key}: {{{field}!{conversion}}} must not convert')

    def test_the_english_catalog_carries_no_chinese(self):
        chinese = re.compile(r'[\u4e00-\u9fff]')
        for key, text in i18n.catalog('en').items():
            self.assertNotRegex(text, chinese, key)

    def test_english_renders_its_own_copy(self):
        self.assertEqual(i18n.set_language('en'), 'en')
        try:
            self.assertEqual(i18n.t('panel.toolbar.quit'), 'Quit')
            self.assertEqual(i18n.t('balls.summary.exact', balls=20), '20 balls')
            self.assertIn('20', i18n.t('rank.text.rank', rank='1,204'))
        finally:
            i18n.set_language(None)


class PoFormatTests(unittest.TestCase):
    """The .po files are the translation source, so their format is part of the contract."""

    def test_every_entry_keys_on_a_context_and_quotes_the_chinese_source(self):
        chinese = re.compile(r'[\u4e00-\u9fff]')
        for path in po_files():
            catalog = po.load(path)
            with self.subTest(catalog=path.name):
                self.assertTrue(catalog.language)
                self.assertTrue(catalog.name)
                for key, source in catalog.sources.items():
                    self.assertTrue(source, key)
                    if key in ('product.name', 'product.credit'):
                        continue  # these two are Latin in Chinese too
                    self.assertRegex(source, chinese, f'{path.name}: {key} msgid is not Chinese')
                self.assertNotIn('', catalog.messages)

    def test_the_message_ids_are_the_chinese_text(self):
        for path in po_files():
            text = path.read_text(encoding='utf-8')
            for key in ('panel.toolbar.quit', 'card.tier.master'):
                block = re.search(rf'msgctxt "{re.escape(key)}"\nmsgid "([^"]*)"', text)
                self.assertIsNotNone(block, f'{path.name}: no entry for {key}')
                self.assertEqual(block.group(1), i18n.catalog(i18n.DEFAULT_LANGUAGE)[key])

    def test_an_empty_msgstr_falls_back_to_the_chinese_source(self):
        """Copying zh_CN.po and translating only some entries must still render."""
        catalog = po.load(LOCALES / 'zh_CN.po')
        catalog.messages['panel.toolbar.quit'] = ''
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'xx.po'
            po.dump(catalog, path)
            reloaded = po.load(path)
        self.assertEqual(reloaded.messages['panel.toolbar.quit'], '退出')

    def test_a_catalog_round_trips_through_dump_and_load(self):
        for path in po_files():
            original = po.load(path)
            with tempfile.TemporaryDirectory() as folder:
                again = pathlib.Path(folder) / path.name
                po.dump(original, again)
                copy = po.load(again)
            self.assertEqual((copy.language, copy.name), (original.language, original.name))
            self.assertEqual(copy.messages, original.messages, path.name)
            self.assertEqual(copy.sources, original.sources, path.name)

    def test_comments_obsolete_entries_and_indented_continuations_are_handled(self):
        text = (
            '# translator note\n'
            '#, fuzzy\n'
            'msgid ""\n'
            'msgstr ""\n'
            '"Language: en\\n"\n'
            '"X-Language-Name: English\\n"\n'
            '\n'
            '#: dib/panel.py:1\n'
            '#~ msgctxt "gone"\n'
            '#~ msgid "旧的"\n'
            '#~ msgstr "old"\n'
            '\n'
            'msgctxt "multi"\n'
            'msgid ""\n'
            '    "第一行\\n"\n'
            '    "第二行"\n'
            'msgstr ""\n'
            '    "First\\n"\n'
            '    "second"\n'
        )
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'x.po'
            path.write_text(text, encoding='utf-8')
            catalog = po.load(path)
        self.assertEqual(catalog.language, 'en')
        self.assertEqual(catalog.messages, {'multi': 'First\nsecond'})
        self.assertEqual(catalog.sources['multi'], '第一行\n第二行')

    def test_a_plural_entry_is_refused_rather_than_silently_truncated(self):
        text = ('msgid ""\nmsgstr ""\n"Language: en\\n"\n\n'
                'msgctxt "n"\nmsgid "一颗"\nmsgid_plural "颗"\nmsgstr[0] "one"\nmsgstr[1] "many"\n')
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'x.po'
            path.write_text(text, encoding='utf-8')
            with self.assertRaises(po.UnsupportedPoFeature):
                po.load(path)

    def test_a_catalog_without_a_language_header_is_refused(self):
        text = 'msgid ""\nmsgstr ""\n"Project-Id-Version: x\\n"\n'
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'x.po'
            path.write_text(text, encoding='utf-8')
            with self.assertRaises(po.PoError):
                po.load(path)
