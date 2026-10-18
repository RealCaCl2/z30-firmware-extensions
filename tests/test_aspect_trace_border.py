"""Trace tails must not weaken committed-record validation."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from read_aspect_trace import decode


def sample(sequence=1,counter=1,version=1,marker=0x31444257):
    data=bytearray(15384)
    struct.pack_into('<6I',data,0,0x31444157,4,counter,96,160,0)
    offset=24+22*160
    struct.pack_into('<4I',data,offset,sequence,5,1,0)
    struct.pack_into('<II',data,offset+16+64,marker,version)
    struct.pack_into('<16I',data,offset+16+72,*range(16))
    struct.pack_into('<II',data,offset+16+136,257 if version==2 else 1,0)
    return data


class BorderTraceTests(unittest.TestCase):
    def test_legacy_menu_sample_without_tail_stays_legacy(self):
        result=decode(sample(marker=0))
        self.assertEqual(len(result['records']),1)
        self.assertNotIn('border',result['records'][0])

    def test_channel_ownership_is_separate_from_native_enable(self):
        item=decode(sample(version=2))['records'][0]['border']
        self.assertEqual(item['rectangle_channel_enabled'],1)
        self.assertTrue(item['rectangle_channel_owned'])
        self.assertEqual(item['stock_grid_enabled'],0)

    def test_future_menu_sample_is_excluded_and_explained(self):
        result=decode(sample(sequence=2,counter=1))
        self.assertEqual(result['records'],[])
        self.assertEqual(result['excluded_menu_samples'][0]['sequence'],2)
        self.assertEqual(result['excluded_menu_samples'][0]['border']['rectangle_channel_disabled'],6)

    def test_unidentified_future_payload_is_not_promoted(self):
        result=decode(sample(sequence=2,counter=1,marker=0))
        self.assertEqual(result['records'],[])
        self.assertNotIn('excluded_menu_samples',result)
