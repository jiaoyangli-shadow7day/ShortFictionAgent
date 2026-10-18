"""Market contract bytes must not depend on process hash randomization."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class MarketSerializationTest(unittest.TestCase):
    def test_contract_order_is_stable_across_hash_seeds_and_input_order(self):
        script = """
import json
from short_fiction_agent.story_studio.market import validate_market_contract
fields = [('payoff', '兑现选择'), ('protagonist_action', '主动选择'),
          ('opening_conflict', '合同冲突'), ('reader_emotion', '期待反制'),
          ('profile_id', 'fanqie_shiqing')]
raw = dict(fields if __import__('os').environ['INPUT_ORDER'] == 'reverse' else reversed(fields))
value = validate_market_contract(raw, ['fanqie_short'], {'description': '喜欢家庭利益冲突的成年读者'}, '选择有结果')
print(json.dumps(value, ensure_ascii=False))
"""
        outputs = []
        for seed, order in [('1', 'reverse'), ('2', 'forward'), ('99', 'reverse')]:
            environment = {**os.environ, 'PYTHONHASHSEED': seed, 'INPUT_ORDER': order}
            output = subprocess.check_output([sys.executable, '-c', script],
                cwd=Path(__file__).resolve().parents[1], env=environment, text=True)
            outputs.append(output)
        self.assertEqual(len(set(outputs)), 1)
        self.assertEqual(list(json.loads(outputs[0])), [
            'profile_id', 'reader_emotion', 'opening_conflict', 'protagonist_action', 'payoff'])


if __name__ == '__main__':
    unittest.main()
