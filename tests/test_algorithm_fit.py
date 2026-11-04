import json
from copy import deepcopy
import unittest

import algorithm_fit as fit
import recovery_search


class AlgorithmFitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((fit.ROOT/'examples/china-city-spatial-input.json').read_bytes())['shenzhen']

    def test_all_factor_corners_preserve_nominal_baseline_and_requested_load(self):
        configs = fit.configurations()
        self.assertEqual(len(configs), 32)
        self.assertEqual(len({tuple(c.items()) for c in configs}), 32)
        for config in configs:
            data, routes, _ = fit.fixture(self.source, 'shenzhen', 0, config)
            checked = recovery_search.evaluate_plan(data, routes)
            self.assertEqual(checked['metrics']['unserved'], 0)
            self.assertEqual(checked['metrics']['late_orders'], 0)
            self.assertAlmostEqual(checked['metrics']['average_volume_utilization'], config['departure_load'])
            self.assertTrue(fit.independent_replay(data, routes, {})['passed'])

    def test_independent_replay_detects_separate_capacity_window_and_return_failures(self):
        data, routes, _ = fit.fixture(self.source, 'shenzhen', 0, fit.configurations()[-1])
        vid, seq = next(iter(routes.items()))
        last = seq[-1]
        scenarios = [
            {'pickup': {last: {'cartons': 10000, 'totes': 10000}}},
            {'extra_service_minutes': {seq[0]: 500}},
            {'road_delay_minutes': {last+'>DEPOT': 500}},
        ]
        for scene, cause in zip(scenarios, ('capacity', 'window', 'shift')):
            replay = fit.independent_replay(data, routes, scene)
            self.assertFalse(replay['passed'])
            self.assertIn(cause, replay['causes'])
            measured = fit.measure(data, routes, [scene])
            self.assertEqual(measured['metrics']['scenario_full_completions'], 0)

    def test_screen_selection_retains_baseline_for_fee_risk_or_insufficient_gain(self):
        data, routes, _ = fit.fixture(self.source, 'shenzhen', 0, fit.configurations()[0])
        base = fit.measure(data, routes, [])
        candidate = deepcopy(base)
        candidate.update(method='robust_local', seed=17)
        candidate['metrics']['scenario_full_completions'] = 1
        self.assertIs(fit.select_candidate(base, [candidate]), base)
        candidate['metrics']['scenario_full_completions'] = 2
        self.assertIs(fit.select_candidate(base, [candidate]), candidate)
        candidate['fee_threshold_sensitivity']['0.9'] += .01
        self.assertIs(fit.select_candidate(base, [candidate]), base)

    def test_seeded_scenario_groups_are_disjoint_and_input_unchanged(self):
        data, _, _ = fit.fixture(self.source, 'shenzhen', 0, fit.configurations()[0])
        original = deepcopy(data)
        groups = [fit.scenarios(data, seed, 32, 'high') for seed in (51000, 61000, 71000)]
        self.assertEqual(groups[0], fit.scenarios(data, 51000, 32, 'high'))
        fingerprints = [{fit.application.fingerprint(s) for s in group} for group in groups]
        self.assertFalse(fingerprints[0] & fingerprints[1] or fingerprints[0] & fingerprints[2]
                         or fingerprints[1] & fingerprints[2])
        self.assertEqual(data, original)

    def test_selection_is_stable_when_runtime_measurements_change(self):
        data, routes, _ = fit.fixture(self.source, 'shenzhen', 0, fit.configurations()[0])
        base = fit.measure(data, routes, [])
        local = deepcopy(base)
        local.update(method='local', seed=17, search_ms=1000)
        local['metrics']['scenario_full_completions'] = 2
        robust = deepcopy(local)
        robust.update(method='robust_local', search_ms=.001)
        self.assertIs(fit.select_candidate(base, [robust, local]), local)
        local['search_ms'], robust['search_ms'] = .001, 1000
        self.assertIs(fit.select_candidate(base, [local, robust]), local)


if __name__ == '__main__':
    unittest.main()
