import argparse
from copy import deepcopy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from short_fiction_agent.story_studio.cli import add_parsers, run
from short_fiction_agent.story_studio.contracts import StoryError
from short_fiction_agent.story_studio.feedback import (
    diagnose_observation, normalize_observation, record_observation, latest_diagnosis,
)
from short_fiction_agent.story_studio.storage import StoryProject, write_json


def observation():
    return dict(platform='fanqie', entry_type='short_story', status='data_visible',
                observed_at='2026-10-03T13:00:00+08:00', evidence='author dashboard screenshot',
                statistics_cutoff='2026-10-03T00:00:00+08:00', aggregation='cumulative_unique',
                metric_scope='fanqie_family_apps', metrics=dict(display_count=46, read_count=10,
                    read_15s=4, read_30s=2, read_60s=2, bottom_count=1))


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name))

    def test_actual_sample_rates_and_missing_metadata(self):
        result = diagnose_observation(observation())
        self.assertAlmostEqual(result['rates']['ctr']['value'], 10 / 46)
        self.assertEqual(result['rates']['read_15s_per_read']['value'], .4)
        self.assertEqual(result['timed_count_gaps'][0]['count_difference'], 6)
        self.assertFalse(result['publication_metadata_complete'])
        self.assertFalse(result['automatic_learning'])
        self.assertEqual(result['commercial_validation'], 'unverified')

    def test_unavailable_is_distinct_from_zero_reads_and_zero_exposure(self):
        for metrics, expected in [({}, 'data_unavailable'),
                ({'display_count': 1, 'read_count': 0, 'read_15s': 0}, 'no_recorded_reading'),
                ({'display_count': 0, 'read_count': 0}, 'no_recorded_exposure')]:
            row = observation(); row['metrics'] = metrics
            report = diagnose_observation(row)
            self.assertEqual(report['stage'], expected)
            self.assertIsNone(report['rates']['read_15s_per_read']['value'])
        row = observation(); row['metrics'] = {'display_count': 46}
        self.assertEqual(diagnose_observation(row)['stage'], 'reading_count_missing')

    def test_counts_reject_bool_float_negative_nan_and_string(self):
        for value in (True, False, 3.0, -1, float('nan'), float('inf'), '46'):
            with self.subTest(value=value):
                row = observation(); row['metrics']['display_count'] = value
                with self.assertRaises(StoryError):
                    record_observation(self.project, row)
        self.assertFalse((self.project.root / 'feedback').exists())

    def test_funnel_validates_across_null_gaps_but_bottom_is_separate(self):
        for patch in ({'read_count': 47}, {'read_15s': None, 'read_30s': 11}, {'bottom_count': 11}):
            row = observation(); row['metrics'].update(patch)
            with self.assertRaises(StoryError):
                normalize_observation(row)
        row = observation(); row['metrics']['bottom_count'] = 3
        # 3 bottom > 2 sixty-second reads: do not invent a metric definition.
        self.assertEqual(normalize_observation(row)['metrics']['bottom_count'], 3)

    def test_legacy_record_read_without_rewriting_or_fabricating_counts(self):
        old = {key: observation()[key] for key in ('platform', 'entry_type', 'status', 'observed_at', 'evidence')}
        old.update(metric_definition='click_rate', value=.5)
        path = self.project.root / 'feedback/platform_records.jsonl'
        path.parent.mkdir(); path.write_text(json.dumps(old) + '\n')
        before = path.read_bytes()
        report = latest_diagnosis(self.project)
        self.assertEqual(report['diagnosis']['stage'], 'data_unavailable')
        self.assertIsNone(report['diagnosis']['sample_counts']['read_count'])
        self.assertEqual(path.read_bytes(), before)

    def test_binding_uses_historical_manifest_and_records_mismatch(self):
        manifest = dict(package_id='old-package', source_artifact_id='draft-1', source_revision=1,
                        source_content_hash='a' * 64)
        write_json(self.project.root / 'exports/old-package/manifest.json', manifest)
        write_json(self.project.root / 'project.json', dict(active_artifact_id='new-draft', active_revision=9))
        row = observation(); row['source_package_id'] = 'old-package'
        row['publication'] = dict(title='posted', body_sha256='a' * 64, ai_disclosure=False)
        _, saved = record_observation(self.project, row)
        self.assertEqual(saved['source_binding']['source_revision'], 1)
        self.assertEqual(saved['source_binding']['status'], 'matches_export')
        self.assertIs(saved['publication']['ai_disclosure'], False)
        row['publication']['body_sha256'] = 'b' * 64
        _, saved = record_observation(self.project, row)
        self.assertEqual(saved['source_binding']['status'], 'published_body_differs')
        self.assertTrue(any('未确认' in item for item in saved['diagnosis']['limitations']))
        row.pop('source_package_id')
        row['source_binding'] = dict(status='matches_export')  # input must not spoof derived binding
        _, saved = record_observation(self.project, row)
        self.assertEqual(saved['source_binding']['status'], 'unbound')

    def test_package_paths_missing_manifest_and_symlinks_rejected(self):
        for package in ('../secret', '/tmp', 'missing', ''):
            row = observation(); row['source_package_id'] = package
            with self.assertRaises(StoryError):
                record_observation(self.project, row)
        outside = self.project.root / 'outside'; outside.mkdir()
        exports = self.project.root / 'exports'; exports.mkdir()
        (exports / 'linked').symlink_to(outside, target_is_directory=True)
        row['source_package_id'] = 'linked'
        with self.assertRaises(StoryError):
            record_observation(self.project, row)

    def test_observation_input_is_not_mutated_and_ai_unknown_stays_null(self):
        row = observation(); original = deepcopy(row)
        _, saved = record_observation(self.project, row)
        self.assertEqual(row, original)
        self.assertIsNone(saved['publication']['ai_disclosure'])
        self.assertIsNone(saved['publication']['body_sha256'])

    def test_empty_legacy_definition_and_nonfinite_value_are_rejected(self):
        # Regression case for independently recorded feedback.
        for definition in ('   ', False, []):
            row = observation(); row.update(value=15, metric_definition=definition)
            with self.assertRaisesRegex(StoryError, '指标定义'):
                normalize_observation(row)
        for value in (float('nan'), float('inf'), True, -1):
            row = observation(); row.update(value=value, metric_definition='clicks')
            with self.assertRaises(StoryError):
                record_observation(self.project, row)

    def test_timestamp_order_and_unknown_timezone(self):
        row = observation(); row['statistics_cutoff'] = '2026-10-04T00:00:00+08:00'
        with self.assertRaisesRegex(StoryError, '不能晚于'):
            normalize_observation(row)
        row['statistics_cutoff'] = '2026-10-02'
        report = diagnose_observation(row)
        self.assertTrue(any('时区' in item for item in report['limitations']))
        row['statistics_cutoff'] = 'not-a-date'
        with self.assertRaisesRegex(StoryError, 'ISO'):
            normalize_observation(row)

    def test_unknown_time_stage_is_not_invented_or_bridged(self):
        row = observation(); row['metrics']['read_15s'] = None
        report = diagnose_observation(row)
        self.assertEqual(len(report['timed_count_gaps']), 1)
        self.assertEqual(report['timed_count_gaps'][0]['from'], 'read_30s')
        self.assertTrue(any('不跨缺失' in item for item in report['limitations']))
        self.assertTrue(any('publication.title' in item for item in report['limitations']))

    def test_last_appended_is_labelled_and_windows_not_aggregated(self):
        row = observation(); record_observation(self.project, row)
        row['statistics_cutoff'] = '2026-10-02T00:00:00+08:00'
        row['aggregation'] = 'daily_unique'; row['metrics']['display_count'] = 50
        record_observation(self.project, row)
        result = latest_diagnosis(self.project)
        self.assertEqual(result['selection'], 'last_appended_not_latest_statistics_cutoff')
        self.assertEqual(result['diagnosis']['sample_counts']['display_count'], 50)

    def test_cli_record_and_analyze_need_no_model_or_project_brief(self):
        parser = argparse.ArgumentParser(); add_parsers(parser.add_subparsers(dest='command'))
        path = self.project.root / 'input.json'; write_json(path, observation())
        for command, extra in [('record-feedback', ['--input', str(path)]), ('analyze-feedback', [])]:
            output = io.StringIO()
            with redirect_stdout(output):
                code = run(parser.parse_args([command, '--project', str(self.project.root), *extra]))
            self.assertEqual(code, 0)
            if command == 'analyze-feedback':
                self.assertEqual(json.loads(output.getvalue())['diagnosis']['rates']['read_60s_per_read']['value'], .2)


if __name__ == '__main__':
    unittest.main()
