"""Native audit recognizes the entry protocol and never erases a selected veto."""
import copy
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.story_studio import entry_pack, submission_guide
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.distribution import PublicationPreparationAgent, audit_workflow
from short_fiction_agent.story_studio.inspiration import seed_fingerprint
from short_fiction_agent.story_studio.selection_review import comparison_input_hash
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class DistributionEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.body = '林禾发现信不见了。' + '她追查记忆交易的线索，决定用自己的记忆换回那封信。' * 31 + '她找回信，也记得代价。'
        self.project, self.llm, self.flow = self.project_for('fanqie', ['fanqie_short'])

    def project_for(self, name, channels):
        project = StoryProject(Path(self.tmp.name) / name)
        project.create(StoryBrief.from_dict({'title': '找信', 'format': 'micro_story', 'channels': channels,
            'length': {'target': 900, 'min': 600, 'max': 1100},
            'author_input': {'premise': '林禾找信', 'locked_facts': []}}))
        llm = ScriptedLLM(self.body)
        return project, llm, StoryWorkflow(project, llm)

    def prepare(self, project=None, flow=None, *, candidate_index=None):
        project, flow = project or self.project, flow or self.flow
        flow.develop(candidate_index=candidate_index)
        self.assertEqual(flow.generate()['decision'], 'passed')
        PublicationPreparationAgent(project, flow.call_json).review(channel='fanqie_short',
            titles=['找信'], synopsis='找回一封信', category='custom')

    def selection_status(self):
        return next(row['status'] for row in audit_workflow(self.project)['checks']
                    if row['check'] == 'comparative_selection')

    def block_second_candidate(self, *, causal=False):
        original = self.flow.call_json
        def call(task, prompt):
            result = original(task, prompt)
            if task == 'candidate_comparative_review':
                row = result['comparisons'][1]
                if causal:
                    source = read_json(self.project.root / 'candidates.json')['candidates'][1]
                    row['feasibility'] = {'decision': 'revise', 'basis': '传输测试：关键交易权限缺失。',
                        'blocking_claims': [{'quote': source['core_scene'], 'reason': '传输测试的独立因果否决。'}]}
                else:
                    row['entry_review']['desire'].update(decision='reject',
                        basis='传输测试的独立入口否决，不能靠其他高分覆盖。')
                    row['entry_review']['decision'] = 'reject'
            return result
        self.flow.call_json = call

    def persist_second_as_explicit_choice(self):
        """Simulate a forged saved choice; recommendation remains an approved first item."""
        selection = read_json(self.project.root / 'selection.json')
        option = read_json(self.project.root / 'candidates.json')['candidates'][1]
        selection.update(candidate_index=1, candidate_id=option['id'], candidate_hash=digest(option),
                         auto_selected=False, selection_method='explicit_choice')
        write_json(self.project.root / 'selection.json', selection)

    def test_native_fanqie_audit_is_complete_and_read_only(self):
        self.prepare()
        comparison = read_json(self.project.root / 'candidate_comparison.json')
        options = read_json(self.project.root / 'candidates.json')['candidates']
        self.assertEqual(comparison['input_hash'], digest({
            'comparison_input': entry_pack.comparison_input_hash(
                self.project.contract(), options, seed_fingerprint(self.project.root)),
            'submission_guide': submission_guide.submission_guide_context(self.project.contract(), 'plan')}))
        before = {str(path): path.read_bytes() for path in self.project.root.rglob('*') if path.is_file()}
        report = audit_workflow(self.project)
        self.assertEqual(report['status'], 'complete', report['checks'])
        self.assertEqual(report['commercial_validation'], 'unverified')
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.project.root.rglob('*') if path.is_file()})

    def test_approved_explicit_choice_can_differ_from_recommendation(self):
        self.prepare(candidate_index=2)
        self.assertEqual(read_json(self.project.root / 'candidate_comparison.json')['recommended_id'], 0)
        self.assertEqual(audit_workflow(self.project)['status'], 'complete')

    def test_generic_native_audit_keeps_existing_comparison_protocol(self):
        project, _, flow = self.project_for('generic', ['generic_text'])
        self.prepare(project, flow)
        options = read_json(project.root / 'candidates.json')['candidates']
        comparison = read_json(project.root / 'candidate_comparison.json')
        self.assertNotIn('entry_review', comparison['comparisons'][0])
        self.assertEqual(comparison['input_hash'], digest({
            'comparison_input': comparison_input_hash(
                project.contract(), options, seed_fingerprint(project.root)),
            'submission_guide': submission_guide.submission_guide_context(project.contract(), 'plan')}))
        self.assertEqual(audit_workflow(project)['status'], 'complete')

    def test_rejected_explicit_choice_is_blocked_in_develop_and_audit(self):
        self.block_second_candidate()
        with self.assertRaisesRegex(StoryError, '入口被独立否决'):
            self.flow.develop(candidate_index=1)
        self.flow.develop(candidate_index=0)
        self.assertEqual(self.selection_status(), 'pass')
        self.persist_second_as_explicit_choice()
        self.assertEqual(self.selection_status(), 'incomplete')

    def test_selected_causal_veto_survives_positive_entry_judgments(self):
        self.block_second_candidate(causal=True)
        self.flow.develop(candidate_index=0)
        self.assertEqual(self.selection_status(), 'pass')
        self.persist_second_as_explicit_choice()
        self.assertEqual(self.selection_status(), 'incomplete')

    def test_old_comparison_hash_cannot_validate_new_entry_selection(self):
        self.flow.develop()
        comparison = read_json(self.project.root / 'candidate_comparison.json')
        options = read_json(self.project.root / 'candidates.json')['candidates']
        comparison['input_hash'] = comparison_input_hash(
            self.project.contract(), options, seed_fingerprint(self.project.root))
        write_json(self.project.root / 'candidate_comparison.json', comparison)
        selection = read_json(self.project.root / 'selection.json')
        selection['comparison_hash'] = digest(comparison)
        write_json(self.project.root / 'selection.json', selection)
        self.assertEqual(self.selection_status(), 'incomplete')

    def test_unchosen_candidate_and_rehashed_forged_evidence_fail_audit(self):
        self.flow.develop()
        original_candidates = read_json(self.project.root / 'candidates.json')
        candidates = copy.deepcopy(original_candidates)
        candidates['candidates'][3]['working_title'] = '比较之后才改写的标题'
        write_json(self.project.root / 'candidates.json', candidates)
        self.assertEqual(self.selection_status(), 'incomplete')
        write_json(self.project.root / 'candidates.json', original_candidates)
        self.assertEqual(self.selection_status(), 'pass')
        comparison = read_json(self.project.root / 'candidate_comparison.json')
        comparison['comparisons'][0]['entry_review']['click']['evidence'][0]['quote'] = '并不在任何候选输入里的原文'
        write_json(self.project.root / 'candidate_comparison.json', comparison)
        selection = read_json(self.project.root / 'selection.json')
        selection['comparison_hash'] = digest(comparison)
        write_json(self.project.root / 'selection.json', selection)
        self.assertEqual(self.selection_status(), 'incomplete')


if __name__ == '__main__':
    unittest.main()
