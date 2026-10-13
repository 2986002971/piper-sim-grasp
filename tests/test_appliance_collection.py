"""Analytical regression for quantized glTF coordinates and retained articulation."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest

path = Path(__file__).resolve().parents[1] / 'models/appliances/collect.py'
spec = importlib.util.spec_from_file_location('appliance_collect', path)
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


def fixture():
    # Interleaved signed-normalized positions and normals, including -32768.
    data = struct.pack('<12h', -32768, 0, 32767, 0, 0, 32767, 16384, 0, -16384, 0, 32767, 0)
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(data)}],
           'bufferViews': [{'buffer': 0, 'byteLength': len(data), 'byteStride': 12}],
           'accessors': [{'bufferView': 0, 'componentType': 5122, 'type': 'VEC3',
                          'count': 2, 'byteOffset': offset, 'normalized': True} for offset in [0, 6]],
           'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 1}}]}],
           'nodes': [{'name': 'door', 'mesh': 0, 'translation': [0.3, 0.5, 0.1]}],
           'animations': [{'name': 'open', 'channels': [{'sampler': 0, 'target': {'node': 0, 'path': 'rotation'}}]}],
           'extensionsRequired': ['KHR_mesh_quantization']}
    encoded = json.dumps(doc).encode(); encoded += b' ' * (-len(encoded) % 4)
    chunks = struct.pack('<II', len(encoded), 0x4e4f534a) + encoded + struct.pack('<II', len(data), 0x004e4942) + data
    return struct.pack('<4sII', b'glTF', 2, 12 + len(chunks)) + chunks


class ApplianceCollectionTests(unittest.TestCase):
    def test_interleaved_normalized_positions_and_normals(self):
        output, converted = collect.normalize_glb(fixture())
        doc, binary = collect.read_glb(output)
        self.assertEqual(converted, [0, 1])
        for i, expected in enumerate([(-1, 0, 1, 16384 / 32767, 0, -16384 / 32767),
                                      (0, 0, 1, 0, 1, 0)]):
            a = doc['accessors'][i]; view = doc['bufferViews'][a['bufferView']]
            actual = struct.unpack_from('<6f', binary, view['byteOffset'])
            for x, y in zip(actual, expected):
                self.assertAlmostEqual(x, y, places=6)
            self.assertEqual(a['componentType'], 5126)
            self.assertNotIn('normalized', a)

    def test_transforms_and_animation_targets_survive(self):
        source = fixture(); original, _ = collect.read_glb(source)
        output, _ = collect.normalize_glb(source); result, _ = collect.read_glb(output)
        self.assertEqual(original['nodes'], result['nodes'])
        self.assertEqual(original['animations'], result['animations'])
        self.assertNotIn('extensionsRequired', result)

    def test_truncated_file_rejected(self):
        with self.assertRaises(ValueError):
            collect.normalize_glb(fixture()[:-1])


if __name__ == '__main__':
    unittest.main()
