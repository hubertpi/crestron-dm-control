"""Offline packaging and protocol regression checks; no hardware required."""
import ast
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'custom_components/crestron_cip_diagnostic'

def load(name):
    spec = importlib.util.spec_from_file_location(name, COMPONENT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class SourceChecks(unittest.TestCase):
    def test_packaging(self):
        self.assertEqual([p.name for p in (ROOT / 'custom_components').iterdir() if p.is_dir()], ['crestron_cip_diagnostic'])
        for path in COMPONENT.rglob('*'):
            if path.suffix == '.py':
                ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            elif path.suffix == '.json':
                json.loads(path.read_text(encoding='utf-8'))
        manifest = json.loads((COMPONENT / 'manifest.json').read_text())
        self.assertEqual(manifest['domain'], COMPONENT.name)
        self.assertEqual(manifest['version'], '0.3.9')
        self.assertEqual(manifest['codeowners'], ['@hubertpi'])
        self.assertEqual(json.loads((ROOT / 'hacs.json').read_text()), {'name': 'Crestron DM Control', 'render_readme': True})
        self.assertEqual((COMPONENT / 'strings.json').read_bytes(), (COMPONENT / 'translations/en.json').read_bytes())

    def test_route_feedback_and_disconnected_outputs(self):
        matrix = load('matrix')
        dump = ('Routing Information for Output Card at Slot 17\nVideo Routed From Input Card at slot 3\n'
                'Routing Information for Output Card at Slot 18\nVideo Disconnected\n'
                'Routing Information for Output Card at Slot 19\nVideo Routed From Input Card at slot 0\n')
        self.assertEqual(matrix.parse_dm_route_info(dump), {17: 3, 18: 0, 19: 0})
        self.assertEqual(matrix.parse_dm_route_info(''), {})

    def test_sony_checksum_and_sharp_status(self):
        probe = load('probe')
        self.assertEqual(probe.decode_sony_power_reply(bytes.fromhex('7000020173')), 'On')
        self.assertEqual(probe.decode_sony_power_reply(bytes.fromhex('7000020072')), 'Off')
        self.assertIsNone(probe.decode_sony_power_reply(bytes.fromhex('7000020172')))
        self.assertEqual(probe.decode_sharp_power_reply(b' 0\r\n'), 'Standby')
        self.assertEqual(probe.decode_sharp_power_reply(b'1\r'), 'On')
        self.assertIsNone(probe.decode_sharp_power_reply(b'ERR\r'))

    def test_raw_serial_limits_and_observed_frame(self):
        probe = load('probe')
        self.assertEqual(probe.build_serial_command_frame('169.254.0.35', b'PWR ON\r'), bytes.fromhex('05000f00000c2001091200505752204f4e0d'))
        for payload in (b'', b'x' * 241):
            with self.assertRaises(ValueError):
                probe.build_serial_command_frame('169.254.0.35', payload)
        with self.assertRaises(ValueError):
            probe.build_serial_command_frame('192.0.2.1', b'x')
        for opcode in (-1, 256):
            with self.assertRaises(ValueError):
                probe.build_serial_command_frame('169.254.0.35', b'x', opcode)

if __name__ == '__main__':
    unittest.main(verbosity=2)
