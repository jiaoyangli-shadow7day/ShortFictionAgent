"""Focused, evidence-bound checks for prose resource and obligation claims.

These are editorial checks, not proof that a story will retain readers. They
force the reviewer to reconstruct concrete claims instead of awarding a broad
coherence score. An independent human veto can still stop a false pass.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from decimal import Decimal

from .contracts import StoryError, digest
from . import editorial_priority


PROTOCOL = "prose-integrity-v9-initial-copyedit-only"
OBLIGATION_TERMS = ("违约金", "赔偿", "罚款", "退款", "退订", "定金")
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
             "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


def enabled(contract: dict) -> bool:
    return contract.get("format") == "short_story" and bool(contract.get("reader_rewards"))


def amount_paragraphs(body: str) -> list[dict]:
    return [{"id": i, "text": line} for i, line in enumerate(body.splitlines())
            if (re.search(r"\d[\d,.]*\s*(?:元|万|千|百)|[一二两三四五六七八九十百千万]+\s*元|"
                          r"[一二两三四五六七八九十百千万]+[百千万][一二两三四五六七八九十百千万]*\s*块|"
                          r"[一二两三四五六七八九十百千万]+块钱|余额", line)
                or (re.search(r"[一二两三四五六七八九十百千万亿]+[百千万][一二两三四五六七八九十百千万亿]*", line)
                    and any(word in line for word in ("花", "付", "垫", "亏", "转账", "全款", "结清", "余额",
                                                       "账户", "账上", "卡里", "账里", "支出"))))]


def obligation_paragraphs(body: str) -> list[dict]:
    return [{"id": i, "text": line} for i, line in enumerate(body.splitlines())
            if any(term in line for term in OBLIGATION_TERMS)]


def resource_prompt(body: str, contract: dict) -> str:
    return (
        editorial_priority.INSTRUCTION +
        "这是初始校对的资金数据提取，不作最终质量阻断，也不要求扩写结算过程。"
        "核算小说中的个人资金。不要评分，也不要把合同总额、未来应收或公司资金算进个人账户。"
        "先逐条标记输入列出的每个金额段落，label只能是opening_balance/"
        "personal_in/personal_out/closing_balance/other/unclear。"
        "每条写id、label、quote（必须是该段连续原文）、amount_yuan（能确定则写数字，否则null）、reason。"
        "amount_yuan是历史字段名，数值沿用原文货币单位；一百五十两银子填150，"
        "不是兑换成人民币，也不能因为单位是两而填null。"
        "多种币种不得直接相加；未说明银票面额、只说大半或剩余等无法量化的段落，"
        "标记unclear或other并解释，不能把提及的原总额冒充该笔支出或最终余额。"
        "若有多个余额，anchor_id须选最后一次已知余额且其后还有个人交易的段落；"
        "closing_id须选结尾明确说余额的段落。transactions写两者之间发生的个人实际出入账"
        "段落id，每个只计一次。金额段落不能漏审。若没有可核对的起止余额，id用null。"
        "输出JSON对象：{\"items\":[...],\"anchor_id\":整数或null,\"closing_id\":整数或null,"
        "\"transaction_ids\":[整数],\"reason\":\"具体口径\"}。不要在JSON外写话。\n"
        + json.dumps({"author_rules": contract.get("author_input", {}),
                      "amount_paragraphs": amount_paragraphs(body),
                      "body": body}, ensure_ascii=False)
    )


def obligation_prompt(body: str, contract: dict) -> str:
    return (
        editorial_priority.INSTRUCTION +
        "这是初始校对的合同数据提取；missing/unclear只保存提醒，不直接决定最终阻断。"
        "真正的主线、输赢或核心回报矛盾由同一正文阅读审查判断，不凭专业手续缺漏另设否决。"
        "核对合同、退订、定金与赔偿归属。只对obligation_paragraphs数组列出的段落逐项输出checks，"
        "必须原样使用其中的id；body仅提供上下文，不能把body全部段落另行编号加入checks。"
        "若obligation_paragraphs为空，输出{\"checks\":[]}。每个待审段落都必须单独审；不要只挑一个容易的句子。"
        "重建签约方、付款方、违约方、获益方及前后交易，不把人物故意嘲讽当成客观真相；"
        "但若嘲讽依赖读者相信错误账目，应判missing。"
        "先判断每笔交易的实际作用：是否影响核心资源的取得、胜负、明确声称的实际余额、"
        "责任或款项归属，或与正文已有条款、金额、付款状态相矛盾。reason须说明这个关联。"
        "普通背景消费或服务已经发生，若省略总价、尾款或结算时间不影响这些判断，"
        "且正文没有相关矛盾，可以supported并解释省略不影响主线；不能仅因交了定金、"
        "已经取得商品或服务，就一律missing或要求补齐账务流程。每个段落仍须覆盖。"
        "只有上述关键判断确实依赖付款、退款或没收条件时，才核对支撑该判断所需的具体环节。"
        "例如正文明确约定付清才能买断关键设备，却仅用定金收据证明买断并决定胜负，"
        "应指出核心权利取得缺少支撑或违反明确条款；必要修订针对这条因果，不扩写全部付款流程。"
        "‘没有写付款’不等于‘没有付款’；缺少关键支撑时说明哪项结论尚不能成立，"
        "不要断言人物未支付。明确余额仍只按原文实际出入账核算，不凭省略虚构尾款或退款。"
        "退订若被用来证明实际退款、损失或对手责任，须核对已写条件与归属；"
        "背景退订未声称这些结果时，不自动要求列出完整退费条款。"
        "不要从‘定金不可退’推断整笔交易已结清，也不要用模型自己的行业常识替正文补合同条款。"
        "输出JSON对象：{\"checks\":[{\"id\":输入段落id,\"verdict\":\"supported/missing/unclear\","
        "\"quote\":\"该段连续原文\",\"basis_ids\":[其他相关输入段落id],"
        "\"reason\":\"具体归属和因果\",\"fix_intent\":\"修订方向\"}]}。"
        "supported的fix_intent写‘保留’，missing的fix_intent写具体修改。"
        "只抄自己正在检查的段落quote，其余证据用basis_ids指向输入，不转述引文。"
        "不得漏掉任何id，别在JSON外写话。\n"
        + json.dumps({"author_rules": contract.get("author_input", {}),
                      "reader_promise": contract.get("reader_promise", ""),
                      "reader_rewards": contract.get("reader_rewards", []),
                      "obligation_paragraphs": obligation_paragraphs(body),
                      "body": body}, ensure_ascii=False)
    )


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None
    return result if result.is_finite() else None


def _chinese_number(token: str) -> Decimal | None:
    token = token.rstrip("元块")
    if len(token) == 1 and token in CN_DIGITS:
        return Decimal(CN_DIGITS[token])
    if not token or not any(char in CN_UNITS for char in token):
        return None
    total = section = number = 0
    last_small_unit = None
    last_unit_index = -1
    for index, char in enumerate(token):
        if char in CN_DIGITS:
            number = CN_DIGITS[char]
        elif char in CN_UNITS:
            unit = CN_UNITS[char]
            last_unit_index = index
            if unit < 10000:
                section += (number or 1) * unit
                last_small_unit = unit
            else:
                total += (section + number or 1) * unit
                last_small_unit = unit
                section = 0
            number = 0
        else:
            return None
    if (token[-1] in CN_DIGITS and last_small_unit and number
            and "零" not in token[last_unit_index + 1:]):
        # Common spoken omission: 六千八=6800, 三万八=38000.
        number *= last_small_unit // 10
    return Decimal(total + section + number)


def money_values(quote: str) -> set[Decimal]:
    """Recognize literal Arabic and common Chinese cash amounts in one quote."""
    values = set()
    for match in re.finditer(r"(?<!\d)(\d[\d,]*(?:\.\d+)?)\s*(元|块|两|万|千|百)", quote):
        scale = {"元": 1, "块": 1, "两": 1, "万": 10000, "千": 1000, "百": 100}[match.group(2)]
        values.add(Decimal(match.group(1).replace(",", "")) * scale)
    for match in re.finditer(r"([零〇一二两三四五六七八九十百千万亿]+)块([零〇一二两三四五六七八九])毛", quote):
        whole = _chinese_number(match.group(1))
        if whole is not None:
            values.add(whole + Decimal(CN_DIGITS[match.group(2)]) / 10)
    for match in re.finditer(r"[零〇一二两三四五六七八九十百千万亿]+(?:元|块)?", quote):
        token = match.group()
        # A trailing 两 is a tael unit; an internal 两 remains the digit two
        # (两百元, 两万块). Never include the unit in Chinese numeral parsing.
        tael_unit = len(token) > 1 and token.endswith("两")
        if not (tael_unit or token.endswith(("元", "块"))) and not any(char in token for char in "百千万亿"):
            continue
        if tael_unit:
            token = token[:-1]
        value = _chinese_number(token)
        if value is not None:
            values.add(value)
    return values


def validate_resource(body: str, response: dict) -> dict:
    if not isinstance(response, dict):
        raise StoryError("资金审查数据无效")
    rows = amount_paragraphs(body)
    by_id = {row["id"]: row["text"] for row in rows}
    items = response.get("items")
    if not isinstance(items, list) or len(items) != len(rows):
        raise StoryError("资金审查未覆盖每个金额段落")
    labels = {"opening_balance", "personal_in", "personal_out", "closing_balance", "other", "unclear"}
    seen = set()
    for item in items:
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] not in by_id:
            raise StoryError("资金审查段落编号无效")
        if item["id"] in seen or item.get("label") not in labels:
            raise StoryError("资金审查标签无效或编号重复")
        seen.add(item["id"])
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote or quote not in by_id[item["id"]]:
            raise StoryError("资金审查引文不在指定段落")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise StoryError("资金审查缺少口径说明")
        if item.get("amount_yuan") is not None and _number(item["amount_yuan"]) is None:
            raise StoryError("资金审查金额无效")
        if item["label"] in {"opening_balance", "closing_balance", "personal_in", "personal_out"}:
            if _number(item.get("amount_yuan")) not in money_values(quote):
                raise StoryError("资金审查金额与指定原文不符")
    if seen != set(by_id):
        raise StoryError("资金审查金额段落缺失")
    by_item = {item["id"]: item for item in items}
    anchor, closing, transactions = (response.get("anchor_id"), response.get("closing_id"),
                                      response.get("transaction_ids"))
    if not isinstance(transactions, list) or any(type(i) is not int for i in transactions):
        raise StoryError("资金审查交易列表无效")
    if len(set(transactions)) != len(transactions):
        raise StoryError("资金审查交易重复")
    if (anchor is None) != (closing is None):
        raise StoryError("资金审查起止余额必须同时给出")
    if anchor is not None:
        if (type(anchor) is not int or type(closing) is not int or anchor >= closing
                or anchor not in by_item or closing not in by_item):
            raise StoryError("资金审查余额锚点无效")
        if (_number(by_item[anchor].get("amount_yuan")) is None
                or _number(by_item[closing].get("amount_yuan")) is None):
            raise StoryError("资金审查余额无法计算")
        for i in transactions:
            if i not in by_item or not anchor < i < closing or by_item[i]["label"] not in {
                    "personal_in", "personal_out"} or _number(by_item[i].get("amount_yuan")) is None:
                raise StoryError("资金审查个人交易无效")
    elif transactions:
        raise StoryError("无余额锚点时不得列余额区间交易")
    if not isinstance(response.get("reason"), str) or not response["reason"].strip():
        raise StoryError("资金审查缺少总体口径")
    return response


def validate_obligation(body: str, response: dict) -> dict:
    if not isinstance(response, dict):
        raise StoryError("合同责任审查数据无效")
    rows = obligation_paragraphs(body)
    by_id = {row["id"]: row["text"] for row in rows}
    checks = response.get("checks")
    if not isinstance(checks, list) or len(checks) != len(rows):
        raise StoryError("合同责任审查未覆盖每个相关段落")
    seen = set()
    for row in checks:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] not in by_id:
            raise StoryError("合同责任段落编号无效")
        if row["id"] in seen or row.get("verdict") not in {"supported", "missing", "unclear"}:
            raise StoryError("合同责任结论无效或编号重复")
        seen.add(row["id"])
        if not isinstance(row.get("quote"), str) or not row["quote"] or row["quote"] not in by_id[row["id"]]:
            raise StoryError("合同责任主引文不在指定段落")
        basis = row.get("basis_ids")
        if not isinstance(basis, list) or any(type(i) is not int or i not in by_id for i in basis):
            raise StoryError("合同责任对照段落编号无效")
        if (not isinstance(row.get("reason"), str) or not row["reason"].strip()
                or not isinstance(row.get("fix_intent"), str)
                or (row["verdict"] != "supported" and not row["fix_intent"].strip())):
            raise StoryError("合同责任审查缺少原因或修订方向")
    if seen != set(by_id):
        raise StoryError("合同责任段落缺失")
    return response


def initial_copyedit_reminders(body: str, resource: dict | None, obligation: dict | None) -> list[dict]:
    """Keep actual arithmetic/obligation findings without promoting them to a veto.

    This extraction does not judge their literary effect. A reader-facing core
    contradiction must be established by the existing source reading instead.
    """
    if resource is None or obligation is None:
        raise StoryError("短篇缺少专项资金或合同责任审查")
    validate_resource(body, resource)
    validate_obligation(body, obligation)
    reminders = []
    anchor, closing = resource["anchor_id"], resource["closing_id"]
    if anchor is not None:
        by_id = {item["id"]: item for item in resource["items"]}
        expected = _number(by_id[anchor]["amount_yuan"])
        for i in resource["transaction_ids"]:
            item = by_id[i]
            amount = _number(item["amount_yuan"])
            expected += amount if item["label"] == "personal_in" else -amount
        stated = _number(by_id[closing]["amount_yuan"])
        if expected != stated:
            reminders.append({'id': 'prose_integrity:balance_mismatch',
                'origin': 'prose_integrity', 'status': 'balance_mismatch',
                'editorial_priority': 'copyedit_detail',
                'reading_effect': '这里只记录核算差异；是否影响核心阅读体验由正文审查判断，不自动阻断。',
                'source_hash': digest(body), 'anchor_id': anchor, 'closing_id': closing,
                'transaction_ids': deepcopy(resource['transaction_ids']),
                'quote': by_id[closing]['quote'], 'reason': resource['reason'],
                'expected_balance': str(expected), 'stated_balance': str(stated),
                'difference': str(stated - expected)})
    for row in obligation['checks']:
        if row['verdict'] != 'supported':
            reminders.append({**deepcopy(row), 'id': f"prose_integrity:obligation:{row['id']}",
                'origin': 'prose_integrity', 'paragraph_id': row['id'], 'status': row['verdict'],
                'editorial_priority': 'copyedit_detail',
                'reading_effect': '这里只保留合同校对判断；核心输赢与回报问题须由正文阅读审查提出，不自动阻断。',
                'source_hash': digest(body)})
    return reminders


def gate(body: str, resource: dict | None, obligation: dict | None) -> list[str]:
    """Validate source-complete audit data; detail findings are initial reminders."""
    initial_copyedit_reminders(body, resource, obligation)
    return []
