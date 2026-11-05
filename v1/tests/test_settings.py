import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from motefield import migrate, schema, settings
from motefield.curves import hermite, make_curve
from motefield.settings import Layer, Settings, SettingError

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


def stack(show=None, look=None, shot=None):
    return Settings(show=Layer('show', show or {}), look=Layer('look', look or {}), shot=Layer('shot', shot or {}))


class Resolve(unittest.TestCase):
    def test_order_and_provenance(self):
        s = stack(show={'units': 'metres', 'density': 10}, look={'density': 200, 'drift': 3},
                  shot={'drift': 5, 'first': 1})
        self.assertEqual(s['units'], 'metres')
        self.assertEqual(s.source('units'), 'show')
        self.assertEqual(s['density'], 200.0)
        self.assertEqual(s.source('density'), 'look')
        self.assertEqual(s['drift'], 5.0)
        self.assertEqual(s.source('drift'), 'shot')
        self.assertEqual(s['eddies'], schema.DEFAULTS['eddies'])
        self.assertEqual(s.source('eddies'), 'default')

    def test_overrides(self):
        s = stack(look={'drift': 3, 'eddies': 2}, shot={'drift': 5, 'first': 1})
        self.assertEqual(s.overrides(), {'drift': ('look', 3.0, 5.0)})

    def test_layer_kind_is_checked(self):
        with self.assertRaises(SettingError):
            Settings(look=Layer('shot', {}))

    def test_unknown_setting(self):
        with self.assertRaises(SettingError):
            Layer('shot', {'nope': 1})


class Masters(unittest.TestCase):
    def test_dials_multiply_and_leave_the_recipe_alone(self):
        s = stack(look={'density': 200, 'ember_rate': 10, 'eddies': 4, 'brightness': 2},
                  shot={'master_amount': 1.5, 'master_air': 0.5, 'master_brightness': 3})
        e = s.engine(1001)
        self.assertEqual(e['density'], 300.0)
        self.assertEqual(e['ember_rate'], 15.0)
        self.assertEqual(e['eddies'], 2.0)
        self.assertEqual(e['brightness'], 6.0)
        self.assertEqual(s['density'], 200.0)                       # the stored value is untouched
        self.assertTrue(e['masters_applied'])

    def test_auto_shadows(self):
        self.assertEqual(stack(shot={'standin': 'wide'}).engine()['shadows'], 'stand-in geometry')
        self.assertEqual(stack(shot={'utility': '/x/u.####.exr'}).engine()['shadows'], 'utility pass')
        self.assertEqual(stack(shot={'shadows': 'none'}).engine()['shadows'], 'none')


class Animation(unittest.TestCase):
    def test_curve_values(self):
        s = stack(shot={'master_brightness': make_curve([(1001, 0.0), (1011, 1.0)])})
        self.assertTrue(s.is_animated('master_brightness'))
        self.assertEqual(s.value('master_brightness', 990), 0.0)     # held before the first key
        self.assertEqual(s.value('master_brightness', 1020), 1.0)    # and after the last
        self.assertAlmostEqual(s.value('master_brightness', 1006), 0.5)
        with self.assertRaises(SettingError):
            s.value('master_brightness')

    def test_curves_are_clamped(self):
        s = stack(shot={'master_brightness': make_curve([(1, 1.0), (2, 0.0), (3, 0.0), (4, 1.0)])})
        for f in range(1, 5):
            self.assertGreaterEqual(s.value('master_brightness', f + 0.5), 0.0)

    def test_vector_curves(self):
        s = stack(look={'tint': make_curve([(1, (1, 0, 0)), (3, (0, 0, 1))], interp='linear')})
        self.assertEqual(s.value('tint', 2), (0.5, 0.0, 0.5))

    def test_sim_settings_cannot_animate(self):
        with self.assertRaises(SettingError):
            Layer('look', {'density': make_curve([(1, 1.0), (2, 2.0)])})

    def test_engine_evaluates_curves(self):
        s = stack(look={'brightness': 2.0}, shot={'master_brightness': make_curve([(1, 0.0), (11, 1.0)], 'linear')})
        self.assertAlmostEqual(s.engine(6)['brightness'], 1.0)

    def test_hermite_matches_its_slope(self):
        F, V = [0.0, 4.0, 10.0, 12.0], [0.0, 3.0, -1.0, 2.0]
        for f in (1.0, 5.5, 11.0):
            v0, d = hermite(F, V, f)
            v1, _ = hermite(F, V, f + 1e-6)
            self.assertAlmostEqual((v1 - v0) / 1e-6, d, places=4)


class Keys(unittest.TestCase):
    def test_what_each_key_follows(self):
        base = stack()
        cases = {  # setting: (prep changes, sim changes, cache changes)
            'light_az': (True, True, True),
            'interior': (True, True, True),
            'density': (False, True, True),
            'master_amount': (False, True, True),
            'master_air': (False, True, True),
            'light_int': (False, False, True),
            'brightness': (False, False, True),
            'fstop': (False, False, True),
            'steady': (False, False, True),
            'fade': (False, False, True),
            'cache': (False, False, False),
            'output_mode': (False, False, False),
            'dof_view': (False, False, False),
            'hold_frame': (False, False, False),
            'plate': (False, False, False),
        }
        new = {'light_az': 40.0, 'interior': False, 'density': 99.0, 'master_amount': 2.0, 'master_air': 2.0,
               'light_int': 2.0, 'brightness': 2.0, 'fstop': 2.8, 'steady': 0.5, 'fade': 10.0, 'cache': '/tmp/x',
               'output_mode': 'holdout check', 'dof_view': False, 'hold_frame': 1010, 'plate': '/p.####.exr'}
        for k, (prep, sim, cache) in cases.items():
            s = stack(shot={k: new[k]})
            self.assertEqual(s.prep_key() != base.prep_key(), prep, k)
            self.assertEqual(s.sim_key() != base.sim_key(), sim, k)
            self.assertEqual(s.cache_key() != base.cache_key(), cache, k)

    def test_shapes_only_push_shapes_change_the_sim(self):
        base = stack()
        hide = stack(shot={'shapes': [{'kind': 'sphere', 'mode': 'hide inside', 'keys': []}]})
        push = stack(shot={'shapes': [{'kind': 'sphere', 'mode': 'push', 'keys': []}]})
        self.assertEqual(hide.sim_key(), base.sim_key())
        self.assertNotEqual(hide.cache_key(), base.cache_key())
        self.assertNotEqual(push.sim_key(), base.sim_key())

    def test_keys_ignore_how_a_number_arrived(self):
        a = stack(look={'density': 5}, shot={'seed': 3.0})
        b = stack(look={'density': '5.0'}, shot={'seed': 3})
        self.assertEqual(a.cache_key(), b.cache_key())

    def test_keys_ignore_which_layer_set_a_value(self):
        self.assertEqual(stack(look={'density': 5}).cache_key(), stack(shot={'density': 5}).cache_key())

    def test_an_animated_value_changes_the_cache_key_only(self):
        base = stack()
        anim = stack(shot={'master_brightness': make_curve([(1, 0.0), (5, 1.0)])})
        self.assertEqual(anim.sim_key(), base.sim_key())
        self.assertNotEqual(anim.cache_key(), base.cache_key())


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def path(self, *p):
        full = os.path.join(self.dir, *p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    def test_round_trip_with_relative_references(self):
        show = Layer('show', {'units': 'metres', 'blades': 7})
        look = Layer('look', {'density': 300, 'brightness': make_curve([(1001, 1.0), (1010, 2.0)])}, name='attic')
        settings.save_layer(show, self.path('show.json'))
        settings.save_layer(look, self.path('looks', 'attic.json'))
        shot = Layer('shot', {'first': 1001, 'last': 1010, 'drift': 4}, look=look.path, show=show.path, name='sh010')
        settings.save_layer(shot, self.path('shots', 'sh010', 'sh010.json'))
        with open(shot.path) as fh:
            raw = json.load(fh)
        self.assertEqual(raw['look'], os.path.join('..', '..', 'looks', 'attic.json'))
        # the whole folder moves: relative references still resolve
        moved = os.path.join(self.dir, 'moved')
        shutil.copytree(self.dir, moved, ignore=shutil.ignore_patterns('moved'))
        s = settings.load(os.path.join(moved, 'shots', 'sh010', 'sh010.json'))
        self.assertEqual(s['units'], 'metres')
        self.assertEqual(s['density'], 300.0)
        self.assertEqual(s['drift'], 4.0)
        self.assertEqual(s.source('blades'), 'show')
        self.assertAlmostEqual(s.value('brightness', 1010), 2.0)
        self.assertEqual(s.cache_key(), Settings(show=show, look=look, shot=shot).cache_key())

    def test_snapshot_stands_alone(self):
        show = Layer('show', {'units': 'metres'})
        look = Layer('look', {'density': 300})
        shot = Layer('shot', {'drift': 4})
        s = Settings(show=show, look=look, shot=shot)
        snap = s.snapshot(name='sh010')
        settings.save_layer(snap, self.path('farm.json'))
        f = settings.load(self.path('farm.json'))
        self.assertEqual(f.cache_key(), s.cache_key())
        self.assertEqual(f['units'], 'metres')
        with open(self.path('farm.json')) as fh:
            raw = json.load(fh)
        self.assertTrue(raw['frozen'])
        self.assertEqual(raw['provenance']['density'], 'look')
        self.assertEqual(raw['keys']['cache'], s.cache_key())
        self.assertNotIn('look', raw)

    def test_unknown_settings_survive_a_round_trip(self):
        settings.write_json(self.path('a.json'), dict(motefield='settings', format=1, layer='look',
                                                      values={'density': 5, 'from_the_future': 3}))
        layer = settings.load_layer(self.path('a.json'))
        self.assertEqual(layer.unknown, {'from_the_future': 3})
        settings.save_layer(layer, self.path('b.json'))
        self.assertEqual(settings.load_layer(self.path('b.json')).unknown, {'from_the_future': 3})
        with self.assertRaises(SettingError):
            settings.load_layer(self.path('a.json'), strict=True)

    def test_newer_format_is_refused(self):
        settings.write_json(self.path('a.json'), dict(motefield='settings', format=99, layer='look', values={}))
        with self.assertRaises(SettingError):
            settings.load_layer(self.path('a.json'))

    def test_wrong_reference_kind(self):
        settings.save_layer(Layer('shot', {}), self.path('notalook.json'))
        settings.save_layer(Layer('shot', {}, look='notalook.json'), self.path('shot.json'))
        with self.assertRaises(SettingError):
            settings.load(self.path('shot.json'))

    def test_v08_file_gets_a_helpful_message(self):
        shutil.copy(os.path.join(FIXTURES, 'v083_defaults.json'), self.path('old.json'))
        with self.assertRaises(SettingError) as cm:
            settings.load(self.path('old.json'))
        self.assertIn('migrate', str(cm.exception))


class Migrate(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(FIXTURES, name)) as fh:
            return json.load(fh)

    def test_v08_defaults_become_an_almost_empty_shot(self):
        shot, look, show, notes = migrate.from_v08(self.load('v083_defaults.json'))
        self.assertIsNone(look)
        # 0.8 defaults equal the new ones, except the shadows choice, which 'auto' now covers
        self.assertEqual(shot.values, {})

    def test_v08_values_carry_over(self):
        old = self.load('v083_campfire.json')
        shot, look, show, notes = migrate.from_v08(old)
        s = Settings(shot=shot)
        self.assertEqual(s['fade_neg'], (1.0, 0.0, 0.0))
        self.assertEqual(s['standin'], 'campfire')            # the 0.8 stand-in files become the built-in stand-in
        self.assertEqual(s['utility'], '')
        self.assertEqual(s['density'], 240.0)
        self.assertEqual(s['holdout_skip'], 'coatstand, walker*')
        self.assertEqual(s['shapes'][0]['mode'], 'push')
        self.assertEqual(s['shadows'], 'auto')
        self.assertEqual(s.engine()['shadows'], 'stand-in geometry')
        for k, v in old.items():
            if k in schema.BY_KEY and k not in ('shadows', 'shapes', 'utility'):
                want = tuple(v) if isinstance(v, list) else v
                self.assertEqual(s[k], schema.coerce(k, want), k)
        self.assertTrue(any('no longer needed' in n for n in notes))
        self.assertTrue(any('viewer-only' in n for n in notes))

    def test_a_real_utility_pass_keeps_its_path(self):
        old = self.load('v083_campfire.json')
        old['utility'] = '/jobs/sq010/sh020/util/util.####.exr'
        shot = migrate.from_v08(old)[0]
        self.assertEqual(shot.values['utility'], old['utility'])
        self.assertNotIn('standin', shot.values)

    def test_split_sends_values_to_their_layers(self):
        shot, look, show, notes = migrate.from_v08(self.load('v083_campfire.json'), split=True)
        self.assertIn('density', look.values)
        self.assertIn('embers', shot.values)
        self.assertIn('box_center', shot.values)
        self.assertNotIn('density', shot.values)
        whole = Settings(shot=migrate.from_v08(self.load('v083_campfire.json'))[0])
        self.assertEqual(Settings(show=show, look=look, shot=shot).cache_key(), whole.cache_key())


if __name__ == '__main__':
    unittest.main()
