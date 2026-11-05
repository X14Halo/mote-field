import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motefield import schema
from motefield.schema import BY_KEY, SettingError, coerce

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

# 0.8 settings that are gone on purpose (migrate.py says what happens to each)
V08_GONE = {'fade_xn', 'fade_xp', 'fade_yn', 'fade_yp', 'fade_zn', 'fade_zp', 'standin_view', 'standin_json', 'fg_fade',
            'movers_baked', 'sim_checkpoints'}


class SchemaTable(unittest.TestCase):
    def test_every_v08_setting_is_kept_or_knowingly_dropped(self):
        with open(os.path.join(FIXTURES, 'v083_defaults.json')) as fh:
            old = json.load(fh)
        missing = [k for k in old if k not in BY_KEY and k not in V08_GONE]
        self.assertEqual(missing, [])

    def test_defaults_are_their_own_coerced_values(self):
        for p in schema.PARAMS:
            self.assertEqual(coerce(p.key, p.default), p.default, p.key)

    def test_defaults_sit_inside_hard_ranges(self):
        for p in schema.PARAMS:
            if p.hard and p.type in ('float', 'int'):
                self.assertTrue(p.hard[0] <= p.default <= p.hard[1], p.key)

    def test_only_render_time_settings_animate(self):
        for p in schema.PARAMS:
            if p.anim:
                self.assertEqual(p.affects, 'look', p.key)

    def test_light_direction_is_per_shot_and_intensity_is_look(self):
        # the light's direction drives the shadows, the bounce patches and the rising warm air: never animatable
        for k in ('light_az', 'light_el', 'shadows', 'interior'):
            self.assertEqual(BY_KEY[k].affects, 'prep', k)
            self.assertFalse(BY_KEY[k].anim, k)
        for k in ('light_int', 'light_k', 'light_rgb'):
            self.assertEqual(BY_KEY[k].affects, 'look', k)
            self.assertTrue(BY_KEY[k].anim, k)

    def test_lens_never_lives_in_the_look_layer(self):
        for p in schema.PARAMS:
            if p.section in ('Lens', 'Bokeh', 'Shutter'):
                self.assertNotEqual(p.layer, 'look', p.key)

    def test_viewer_and_node_settings_are_not_hashed(self):
        for p in schema.PARAMS:
            if p.ws == 'view':
                self.assertEqual(p.affects, 'view', p.key)
        self.assertNotIn('view', schema.HASHED)
        self.assertNotIn('node', schema.HASHED)

    def test_dials(self):
        self.assertEqual(len(schema.DIALS), 4)
        for k in schema.DIALS:
            self.assertEqual(BY_KEY[k].level, 'basic')


class Coerce(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(coerce('density', '250'), 250.0)
        self.assertIsInstance(coerce('density', 250), float)
        self.assertEqual(coerce('density', -5), 0.0)                 # clamped to the hard range
        self.assertEqual(coerce('steady', 3), 1.0)
        self.assertEqual(coerce('seed', 7.0), 7)
        self.assertIsInstance(coerce('seed', 7.0), int)
        with self.assertRaises(SettingError):
            coerce('seed', 7.5)
        with self.assertRaises(SettingError):
            coerce('density', 'lots')
        with self.assertRaises(SettingError):
            coerce('density', float('nan'))

    def test_bools(self):
        for v, want in (('on', True), ('0', False), ('', False), (1, True), (0.0, False), ('TRUE', True)):
            self.assertIs(coerce('holdout', v), want)
        with self.assertRaises(SettingError):
            coerce('holdout', 'maybe')

    def test_enums(self):
        self.assertEqual(coerce('units', 'metres'), 'metres')
        with self.assertRaises(SettingError):
            coerce('units', 'parsecs')

    def test_vectors(self):
        self.assertEqual(coerce('box_center', '1 2 3'), (1.0, 2.0, 3.0))
        self.assertEqual(coerce('box_center', '1, 2, 3'), (1.0, 2.0, 3.0))
        self.assertEqual(coerce('box_center', [1, 2, 3]), (1.0, 2.0, 3.0))
        self.assertEqual(coerce('box_size', [-1, 2, 3]), (0.0, 2.0, 3.0))
        with self.assertRaises(SettingError):
            coerce('box_center', [1, 2])

    def test_json(self):
        self.assertEqual(coerce('shapes', '[{"kind": "box"}]'), [{'kind': 'box'}])
        self.assertEqual(coerce('shapes', ''), [])
        with self.assertRaises(SettingError):
            coerce('shapes', '[{')

    def test_unknown(self):
        with self.assertRaises(SettingError):
            coerce('nope', 1)


class Generated(unittest.TestCase):
    def test_json_lists_every_setting(self):
        d = json.loads(schema.to_json())
        self.assertEqual([p['key'] for p in d['params']], [p.key for p in schema.PARAMS])
        self.assertEqual(set(d['masters']), set(schema.MASTERS))

    def test_markdown_lists_every_setting(self):
        md = schema.to_markdown()
        for p in schema.PARAMS:
            self.assertIn('`%s`' % p.key, md)


if __name__ == '__main__':
    unittest.main()
