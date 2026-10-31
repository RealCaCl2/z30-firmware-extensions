import unittest
from unittest.mock import patch

from z30_extensions import assembly


class AssemblyCacheTests(unittest.TestCase):
    def test_hit_needs_no_keystone_and_miss_reports_it(self):
        key = assembly.cache_key('nop;', 0x1000)
        with patch.object(assembly, '_cache', {key: b'\x00\xbf'}), patch.dict('sys.modules', {'keystone': None}):
            self.assertEqual(assembly.assemble('nop;', 0x1000, 2), b'\x00\xbf')
            with self.assertRaisesRegex(ValueError, 'Keystone is not installed'):
                assembly.assemble('nop;', 0x1002, 2)

    def test_hit_still_enforces_capacity(self):
        key = assembly.cache_key('nop;', 0x1000)
        with patch.object(assembly, '_cache', {key: b'\x00\xbf'}):
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                assembly.assemble('nop;', 0x1000, 1)


if __name__ == '__main__':
    unittest.main()
