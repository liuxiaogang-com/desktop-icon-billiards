"""Pure helpers of the LAN dialog: room lines, the default room and code clean-up.

No Tk window and no sockets are created here, so the module runs on any machine.
"""
import unittest

from dib.versus import clean_code, describe_room, format_addresses, pick_default


class CleanCodeTests(unittest.TestCase):
    def test_upper_cases_and_keeps_at_most_four_characters(self):
        self.assertEqual(clean_code('ab2c'), 'AB2C')
        self.assertEqual(clean_code(' A B 2 C D '), 'AB2C')
        self.assertEqual(clean_code('ABCDEF'), 'ABCD')

    def test_drops_characters_that_never_appear_in_a_room_code(self):
        self.assertEqual(clean_code('A0O1I234'), 'A234')
        self.assertEqual(clean_code('i-o'), '')

    def test_blank_values_are_empty(self):
        self.assertEqual(clean_code(None), '')
        self.assertEqual(clean_code(''), '')
        self.assertEqual(clean_code('   '), '')


class DescribeRoomTests(unittest.TestCase):
    def test_names_room_and_address_on_one_line(self):
        text = describe_room({'host': '192.168.1.7', 'room': 'ab2c', 'name': 'PC-A'})
        self.assertEqual(text, 'PC-A · 房间 AB2C · 192.168.1.7')
        self.assertNotIn('\n', text)

    def test_falls_back_for_a_nameless_room(self):
        self.assertEqual(describe_room({'host': '10.0.0.2', 'room': 'WXYZ'}),
                         '未命名电脑 · 房间 WXYZ · 10.0.0.2')

    def test_a_broken_entry_never_raises(self):
        self.assertEqual(describe_room(None), '未知房间')
        self.assertEqual(describe_room('192.168.1.7'), '未知房间')
        self.assertEqual(describe_room({}), '未命名电脑 · 房间未知')


class PickDefaultTests(unittest.TestCase):
    def test_first_complete_room_wins(self):
        rooms = [{'host': '10.0.0.2', 'room': 'ab2c', 'name': 'PC-B'}]
        self.assertEqual(pick_default(rooms), ('10.0.0.2', 'AB2C'))

    def test_incomplete_entries_are_skipped(self):
        rooms = [{'room': 'ABCD'}, {'host': '10.0.0.3'}, {'host': '10.0.0.4', 'room': 'wxyz'}]
        self.assertEqual(pick_default(rooms), ('10.0.0.4', 'WXYZ'))

    def test_nothing_found_gives_empty_strings(self):
        self.assertEqual(pick_default([]), ('', ''))
        self.assertEqual(pick_default(None), ('', ''))


class AddressLineTests(unittest.TestCase):
    def test_joins_the_local_addresses(self):
        self.assertEqual(format_addresses(['192.168.1.5', '10.0.0.2']),
                         '192.168.1.5 / 10.0.0.2')

    def test_blank_entries_are_ignored(self):
        self.assertEqual(format_addresses(['', '  192.168.1.5  ']), '192.168.1.5')

    def test_no_address_says_what_to_check(self):
        self.assertIn('未找到局域网地址', format_addresses([]))
        self.assertIn('未找到局域网地址', format_addresses(None))


if __name__ == '__main__':
    unittest.main()
