import binascii
import hashlib
import unittest
from pathlib import Path

from z30_extensions.build import build
from z30_extensions.container import detect_model, extract_eg, Z7_CONTAINER_SHA256

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / 'Z_7_0380.bin'
STOCK_FULL_SHA256 = '7f59d8993080203a12692cf6927a0a8218786d3f72230c04174f1563c506a6f6'
STOCK_MAIN_SHA256 = 'e6df1764323fb1c287159dd3ce1c84ac93c72b96ba6eb09c070295c093fecbff'


@unittest.skipUnless(FIRMWARE.exists(), 'local Z_7_0380.bin not present')
class Z7IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container = FIRMWARE.read_bytes()
        cls.stock = extract_eg(cls.container)

    def test_container_detection_and_extraction(self):
        self.assertEqual(hashlib.sha256(self.container).hexdigest(), Z7_CONTAINER_SHA256)
        self.assertEqual(detect_model(self.container), 'z7')
        self.assertEqual(len(self.stock), 33052090)
        self.assertEqual(hashlib.sha256(self.stock).hexdigest(), STOCK_FULL_SHA256)
        self.assertEqual(hashlib.sha256(self.stock[:-2]).hexdigest(), STOCK_MAIN_SHA256)
        self.assertEqual(binascii.crc_hqx(self.stock, 0), 0)

    def test_supported_presets_rebuild_cleanly(self):
        for preset in ('liveview-dual', 'm900', 'shutter-sound', 'full'):
            with self.subTest(preset=preset):
                output, report = build(self.stock, preset, model='z7')
                self.assertEqual(binascii.crc_hqx(output, 0), 0)
                self.assertEqual(report['model'], 'Nikon Z7')
                self.assertEqual(report['firmware'], 'C 3.80')
                self.assertEqual(report['preset'], preset)
                self.assertGreater(len(output), len(self.stock))
                if preset == 'liveview-dual':
                    self.assertIn('liveview_dual', report['features'])
                    self.assertNotIn('m900', report['features'])
                    self.assertNotIn('shutter_sound', report['features'])
                if preset == 'm900':
                    self.assertIn('m900', report['features'])
                    self.assertNotIn('liveview_dual', report['features'])
                    self.assertNotIn('shutter_sound', report['features'])
                if preset == 'shutter-sound':
                    self.assertIn('shutter_sound', report['features'])
                    self.assertNotIn('liveview_dual', report['features'])
                    self.assertNotIn('m900', report['features'])
                if preset == 'full':
                    self.assertIn('liveview_dual', report['features'])
                    self.assertIn('m900', report['features'])
                    self.assertIn('shutter_sound', report['features'])


if __name__ == '__main__':
    unittest.main()
