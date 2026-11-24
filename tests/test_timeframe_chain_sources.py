import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('chain', pathlib.Path(__file__).resolve().parents[1] / 'nestdaq' / 'scripts' / 'run-timeframe-chain.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SETUP = {'data_sources': {'receiver': {}, 'hrtdc30': {}, 'hrtdc31': {}}}

class SourceSelectionTests(unittest.TestCase):
    def test_all_sources_in_setup_order(self):
        self.assertEqual(m.select_sources(SETUP), {'ReceiverSTF': 'receiver', 'Hrtdc30STF': 'hrtdc30', 'Hrtdc31STF': 'hrtdc31'})
    def test_subset_keeps_given_order(self):
        self.assertEqual(list(m.select_sources(SETUP, ['hrtdc31', 'receiver']).values()), ['hrtdc31', 'receiver'])
    def test_unknown_or_repeated_source_rejected(self):
        for names in (['hrtdc99'], ['receiver', 'receiver']):
            with self.assertRaises(ValueError):m.select_sources(SETUP, names)
    def test_colliding_names_rejected(self):
        with self.assertRaises(ValueError):m.select_sources({'data_sources': {'hr-tdc': {}, 'hr_tdc': {}}})
    def test_service_names(self):
        self.assertEqual(m.service_base('hrtdc-30'), 'Hrtdc30')

if __name__ == '__main__':unittest.main()
