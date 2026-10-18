import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from short_fiction_agent.story_studio import prose_integrity as pi
from short_fiction_agent.story_studio import chief_first_read as first
from short_fiction_agent.story_studio.contracts import StoryError, digest
from short_fiction_agent.story_studio.quality import review_rubric_hash
from tests.narrative_fixtures import narrative_response


class ProseIntegrityTests(unittest.TestCase):
    def test_tael_unit_is_not_the_digit_two(self):
        self.assertEqual(pi.money_values("五十两雪花银，一百两银票，合计一百五十两"),
                         {Decimal(50), Decimal(100), Decimal(150)})
        self.assertEqual(pi.money_values("五十二两"), {Decimal(52)})
        self.assertEqual(pi.money_values("两两银子"), {Decimal(2)})
        self.assertEqual(pi.money_values("150两"), {Decimal(150)})
        self.assertEqual(pi.money_values("两百元，两万块"), {Decimal(200), Decimal(20000)})

    def test_tael_audit_still_rejects_wrong_or_unstated_totals(self):
        body = "我掏出五十两雪花银和一百两银票，共计一百五十两。"
        response = {"items": [{"id": 0, "label": "opening_balance", "quote": body,
                              "amount_yuan": 150, "reason": "明确合计"}],
                    "anchor_id": None, "closing_id": None,
                    "transaction_ids": [], "reason": "仅有初始资金"}
        pi.validate_resource(body, response)
        response["items"][0]["amount_yuan"] = 152
        with self.assertRaisesRegex(StoryError, "金额与指定原文不符"):
            pi.validate_resource(body, response)
        response["items"][0]["amount_yuan"] = 150
        response["items"][0]["quote"] = "五十两雪花银和一百两银票"
        with self.assertRaisesRegex(StoryError, "金额与指定原文不符"):
            pi.validate_resource(body, response)

    def test_common_chinese_money_values(self):
        self.assertEqual(pi.money_values("六千八全款结清"), {Decimal(6800)})
        self.assertEqual(pi.money_values("一百零二元"), {Decimal(102)})
        self.assertEqual(pi.money_values("二十二万六千四百块"), {Decimal(226400)})
        self.assertIn(Decimal("228620.5"), pi.money_values("二十二万八千六百二十块五毛"))
        self.assertEqual(len(pi.amount_paragraphs("走我的账户，六千八全款结清。")), 1)
        self.assertEqual(pi.amount_paragraphs("漂亮的腿摔青了一块。"), [])

    def setUp(self):
        self.body = "\n".join([
            "短信写着：转入人民币300,000.00元，当前余额301,420.50元。",
            "我花两万八千块买调度台。",
            "我又付三万八千块定金。",
            "我花六千八百块买纱。",
            "老板退订灯组，违约金从我们公司账上扣。",
            "我对对手说：剧院的违约金可都不便宜。",
            "我卡里余额二十二万六千四百块。",
        ])

    def test_account_balance_without_currency_suffix_is_audited(self):
        body = "\n".join([
            "监管账户里有三千八百万。",
            "我付了四百五十万到期借款。",
            "又支出了三百五十万。",
            "之后还有三千万留在监管账户，不能随意挥霍。",
        ])
        rows = pi.amount_paragraphs(body)
        self.assertEqual([row['id'] for row in rows], [0, 1, 2, 3])
        labels = ['opening_balance', 'personal_out', 'personal_out', 'closing_balance']
        amounts = [38000000, 4500000, 3500000, 30000000]
        response = {
            'items': [{'id': row['id'], 'label': labels[row['id']],
                       'quote': row['text'], 'amount_yuan': amounts[row['id']],
                       'reason': '分红代付账逐笔核对'} for row in rows],
            'anchor_id': 0, 'closing_id': 3, 'transaction_ids': [1, 2],
            'reason': '监管分红支出后仍归主角的余额',
        }
        self.assertEqual(pi.gate(body, response, {'checks': []}), [])
        wrong = body.replace('三千万留', '三千一百万留')
        response['items'][-1]['quote'] = wrong.splitlines()[-1]
        response['items'][-1]['amount_yuan'] = 31000000
        self.assertEqual(pi.gate(wrong, response, {'checks': []}), [])
        reminders = pi.initial_copyedit_reminders(wrong, response, {'checks': []})
        self.assertEqual([row['id'] for row in reminders], ['prose_integrity:balance_mismatch'])
        self.assertEqual(reminders[0]['difference'], '1000000')
        self.assertEqual(pi.amount_paragraphs('她重复练习了三千万次。'), [])

    def resource(self, ending=226400):
        labels = {0: ("opening_balance", 301420.5), 1: ("personal_out", 28000),
                  2: ("personal_out", 38000), 3: ("personal_out", 6800),
                  4: ("other", None), 5: ("other", None),
                  6: ("closing_balance", ending)}
        return {"items": [{"id": row["id"], "label": labels[row["id"]][0],
                           "amount_yuan": labels[row["id"]][1], "quote": row["text"],
                           "reason": "逐项核对"} for row in pi.amount_paragraphs(self.body)],
                "anchor_id": 0, "closing_id": 6, "transaction_ids": [1, 2, 3],
                "reason": "以转账后的真实余额为锚点，逐项扣除个人支出"}

    def obligation(self, last="missing"):
        return {"checks": [{"id": row["id"], "verdict": last if row["id"] == 5 else "supported",
                            "quote": row["text"], "basis_ids": [4],
                            "reason": "由公司退订，不由对手负担" if row["id"] == 5 else "归属清楚",
                            "fix_intent": "修正台词" if row["id"] == 5 else "保留"}
                           for row in pi.obligation_paragraphs(self.body)]}

    def test_wrong_balance_and_penalty_owner_are_initial_reminders_without_blocking(self):
        resource, obligation = self.resource(), self.obligation()
        self.assertEqual(pi.gate(self.body, resource, obligation), [])
        reminders = pi.initial_copyedit_reminders(self.body, resource, obligation)
        self.assertEqual([row['id'] for row in reminders],
                         ["prose_integrity:balance_mismatch", "prose_integrity:obligation:5"])
        self.assertEqual(reminders[0]['expected_balance'], '228620.5')
        self.assertEqual(reminders[0]['stated_balance'], '226400')
        self.assertEqual(reminders[0]['difference'], '-2220.5')
        self.assertEqual(reminders[1]['status'], 'missing')
        self.assertEqual(reminders[1]['paragraph_id'], 5)
        self.assertEqual(obligation['checks'][-1]['verdict'], 'missing')
        self.assertTrue(all(row['editorial_priority'] == 'copyedit_detail' for row in reminders))

    def test_valid_balance_and_ownership_pass(self):
        correct = self.body.replace("二十二万六千四百块", "228,620.50元")
        resource = self.resource(228620.5)
        resource["items"][-1]["quote"] = correct.splitlines()[-1]
        obligation = self.obligation("supported")
        self.assertEqual(pi.gate(correct, resource, obligation), [])
        self.assertEqual(pi.initial_copyedit_reminders(correct, resource, obligation), [])

    def test_missing_obligation_paragraph_fails_closed(self):
        response = self.obligation()
        response["checks"].pop()
        with self.assertRaisesRegex(StoryError, "未覆盖"):
            pi.validate_obligation(self.body, response)

    def test_absent_or_invalid_audit_data_still_fails_integrity_checks(self):
        with self.assertRaisesRegex(StoryError, '缺少专项'):
            pi.gate(self.body, None, self.obligation())
        with self.assertRaisesRegex(StoryError, '数据无效'):
            pi.gate(self.body, [], self.obligation())
        resource = self.resource()
        resource['items'].pop()
        with self.assertRaisesRegex(StoryError, '未覆盖'):
            pi.gate(self.body, resource, self.obligation())


class ObligationMaterialityTests(unittest.TestCase):
    """Evidence and gate controls; these do not simulate a model's semantic judgment."""

    def resources(self, body, actual_out=None):
        actual_out = actual_out or {}
        return {"items": [{"id": row["id"], "label": "personal_out" if row["id"] in actual_out else "other",
                           "amount_yuan": actual_out.get(row["id"]), "quote": row["text"],
                           "reason": "只记录原文已发生的出账；报价、条件与省略不视为实际出账"}
                          for row in pi.amount_paragraphs(body)],
                "anchor_id": None, "closing_id": None, "transaction_ids": [],
                "reason": "本对照没有声明起止余额，不补造账目"}

    def background(self):
        return "\n".join([
            "我付了三百元定金，摄影师第二天来给我们拍合照。",
            "她退订了原来的蛋糕，换成我们初见时吃过的口味。",
            "镜头亮起时，她先牵住我的手，我们终于不再躲着彼此。",
        ])

    def test_background_service_keeps_all_checks_without_forcing_payment_or_refund_details(self):
        body = self.background()
        rows = pi.obligation_paragraphs(body)
        self.assertEqual([row["id"] for row in rows], [0, 1])
        response = {"checks": [{"id": row["id"], "verdict": "supported", "quote": row["text"], "basis_ids": [],
                                "reason": "这是背景服务或选择，未用它证明买断、余额、退款到账或他人赔偿；省略结算不改变关系选择",
                                "fix_intent": "保留"} for row in rows]}
        self.assertEqual(pi.gate(body, self.resources(body, {0: 300}), response), [])
        response["checks"].pop()
        with self.assertRaisesRegex(StoryError, "未覆盖"):
            pi.validate_obligation(body, response)

    def test_core_buyout_contradiction_requires_source_reading_instead_of_automatic_legal_veto(self):
        body = "\n".join([
            "我付了三百元定金，老板让我把设备先搬走试用。",
            "合同写明：定金不等于买断，三万元付清后才转移所有权。",
            "我拿着定金收据宣告已经买断设备，凭所有权把对手赶出场地。",
        ])
        rows = pi.obligation_paragraphs(body)
        response = {"checks": [{"id": row["id"], "verdict": "missing" if row["id"] == 2 else "supported",
                                "quote": row["text"], "basis_ids": [0, 1] if row["id"] == 2 else [],
                                "reason": ("核心胜负依赖所有权，正文却把定金收据当买断凭证，与已写的付清条件冲突；缺取得权利的支撑，不能断言从未付款"
                                           if row["id"] == 2 else "已写的定金出账或买断条款可核对"),
                                "fix_intent": "修正定金收据与买断权利的因果，或改用已建立的真实筹码" if row["id"] == 2 else "保留"}
                               for row in rows]}
        self.assertEqual(pi.gate(body, self.resources(body, {0: 300}), response), [])
        self.assertEqual(pi.initial_copyedit_reminders(body, self.resources(body, {0: 300}), response)[0]['status'], 'missing')
        def stage(task, prompt, allowed, *, first_read_plan):
            reading = narrative_response(prompt)
            reading['burden'].update(status='thin', evidence_ids=['p1', 'p2'],
                editorial_priority='reading_core',
                reading_effect='明确尚未买断，却凭同一凭证完成反击，读者看不懂主角如何赢。',
                reason='实际赢法与正文已有事实直接冲突。',
                fix_intent='改用已取得的真实筹码完成反击，不扩写专业手续。')
            checked = first.validate_response(reading, body, first_read_plan=first_read_plan)
            return checked, {'input_hash': digest(prompt), 'response_hash': digest(reading), 'response': reading}
        record = first.run_first_read(body, {}, stage=stage)['first_read']
        final = first.final_gate({'recommendation': 'ready', 'issues': []}, body, {}, record)
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(final['issues'][0]['editorial_priority'], 'reading_core')
        self.assertEqual(final['issues'][0]['severity'], 'blocker')
        response["checks"][-1]["quote"] = "我根本没有付尾款。"
        with self.assertRaisesRegex(StoryError, "引文不在"):
            pi.validate_obligation(body, response)

    def test_omitted_background_tail_cannot_be_invented_to_reconcile_wrong_balance(self):
        body = "\n".join(["我卡里余额一千元。", "我付了三百元定金，摄影师来拍照。", "我卡里还剩六百元。"])
        labels = ["opening_balance", "personal_out", "closing_balance"]
        amounts = [1000, 300, 600]
        resource = {"items": [{"id": row["id"], "label": labels[row["id"]], "amount_yuan": amounts[row["id"]],
                               "quote": row["text"], "reason": "按正文已声明的余额及实际出账核算"}
                              for row in pi.amount_paragraphs(body)],
                    "anchor_id": 0, "closing_id": 2, "transaction_ids": [1], "reason": "不能假设多付了一百元尾款"}
        obligation = {"checks": [{"id": 1, "verdict": "supported", "quote": body.splitlines()[1], "basis_ids": [],
                                   "reason": "定金出账有依据；未交代背景服务尾款不证明欠款，明确余额单独核算", "fix_intent": "保留"}]}
        self.assertEqual(pi.gate(body, resource, obligation), [])
        reminder = pi.initial_copyedit_reminders(body, resource, obligation)[0]
        self.assertEqual(reminder['expected_balance'], '700')
        self.assertEqual(reminder['difference'], '-100')
        self.assertEqual(reminder['transaction_ids'], [1])

    def test_prompt_keeps_full_context_and_changes_universal_payment_rule(self):
        body = self.background()
        contract = {"author_input": {"premise": "两人决定公开关系", "locked_facts": ["合照发生在第二天"]},
                    "reader_promise": "兑现主动牵手的关系改变", "reader_rewards": [{"id": "choice", "required": True}]}
        prompt = pi.obligation_prompt(body, contract)
        instructions, payload = prompt.split("\n", 1)
        data = json.loads(payload)
        self.assertEqual(data["body"], body)
        self.assertEqual(data["obligation_paragraphs"], pi.obligation_paragraphs(body))
        self.assertEqual(data["author_rules"], contract["author_input"])
        self.assertEqual(data["reader_promise"], contract["reader_promise"])
        self.assertEqual(data["reader_rewards"], contract["reader_rewards"])
        self.assertIn("普通背景消费或服务", instructions)
        self.assertIn("‘没有写付款’不等于‘没有付款’", instructions)
        self.assertIn("核心资源的取得、胜负、明确声称的实际余额", instructions)
        self.assertIn("不要用模型自己的行业常识替正文补合同条款", instructions)
        self.assertIn("missing/unclear只保存提醒", instructions)
        self.assertNotIn("未交代则该定金与取得设备", instructions)

    def test_protocol_change_invalidates_previous_review_rubric(self):
        updated = review_rubric_hash()
        with patch.object(pi, "PROTOCOL", "prose-integrity-v7"):
            previous = review_rubric_hash()
        self.assertNotEqual(updated, previous)


if __name__ == "__main__":
    unittest.main()
