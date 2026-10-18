"""Ground the motive of a changed life in the opening, not in the plan."""
import re

from .contracts import StoryError, visible_count

PROTOCOL = "narrative-setup-v2"
REQUIREMENTS = {
    "past_consequence": "重生故事：正文交代前次经历及其具体后果，让本次改选有依据；不强制惨死、复仇或长篇回忆。",
    "return_anchor": "重生故事：正文明确回到了哪个时刻，并让读者区分前次经历与当前现场；不能仅出现‘上一世’三个字。",
    "arrival_anchor": "穿越故事：正文明确原处境与来到的新处境；不把穿越一律写成前世受害后复仇。",
    "changed_choice": "正文建立主角为何现在作出这个选择，情绪强度与已知经历相称；不能用反击动作或口号代替动机。",
}
INSTRUCTION = (
    "若narrative_requirements非空，另输出narrative_checks数组，完整覆盖其中id。"
    "每项含id、verdict（supported/missing/unclear）、reason、fix_intent、"
    "evidence_paragraph_indices（opening_paragraphs中的整数编号数组）。"
    "每个要求都必须单独返回，包括changed_choice。reason和fix_intent均须非空；"
    "supported的fix_intent可写‘保留当前呈现’，不能留空。"
    "supported须有非空正文依据；missing/unclear给具体缺口，可用空证据数组。"
    "只按开头已出现的内容判断，不能用作者合同、大纲或后文补齐读者尚不知道的事。"
    "题材本身不可被删除来规避缺陷；必要背景随当前动作补入。\n"
)


def requirements(contract):
    # Avoid lists are excluded, and prohibitions embedded in the premise must
    # not become positive genre requests. Stop at clause boundaries so a later
    # affirmative premise (e.g. 不用重生，但主角穿越) still triggers its checks.
    genre = contract.get("genre") or {}
    premise = str((contract.get("author_input") or {}).get("premise", ""))
    premise = re.sub(
        r"(?:不(?:换成|改成|要|用|加|写|含|设|安排|引入|采用|添加|包含|涉及|是|属于)|没有|无|非|避免|排除)"
        r"[^，,。！？；;\n]*(?=，|,|。|！|？|；|;|\n|$)",
        "", premise,
    )
    text = " ".join(str(x) for x in (
        premise,
        genre.get("primary", ""), genre.get("custom_description", ""),
        *(genre.get("secondary") or []),
        *((contract.get("research_preferences") or {}).get("reader_tags") or []),
    ))
    keys = (["past_consequence", "return_anchor", "changed_choice"]
            if "重生" in text or "上一世" in text else
            ["arrival_anchor", "changed_choice"] if "穿越" in text else [])
    return [{"id": key, "requirement": REQUIREMENTS[key]} for key in keys]


def opening_paragraphs(body, target=1200):
    """A complete-paragraph observation window; this is not a platform threshold."""
    paragraphs, count = [], 0
    for line in body.splitlines():
        if not line.strip():
            continue
        paragraphs.append(line)
        count += visible_count(line)
        if count >= target:
            break
    return paragraphs


def validate_checks(rows, expected, paragraphs):
    ids = [item["id"] for item in expected]
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or sorted(str(row.get("id")) for row in rows) != sorted(ids)):
        raise StoryError("开篇动机检查必须完整覆盖要求，ID不得缺失或重复")
    result = []
    for row in rows:
        if row.get("verdict") not in {"supported", "missing", "unclear"}:
            raise StoryError("开篇动机检查结论无效")
        if any(not isinstance(row.get(k), str) or not row[k].strip()
               for k in ("reason", "fix_intent")):
            raise StoryError("开篇动机检查缺少依据或修订方向")
        indices = row.get("evidence_paragraph_indices")
        if (not isinstance(indices, list) or len(set(str(i) for i in indices)) != len(indices)
                or any(type(i) is not int or not 0 <= i < len(paragraphs) for i in indices)
                or (row["verdict"] == "supported" and not indices)):
            raise StoryError("开篇动机检查依据必须来自开篇，不能用后文或大纲代替")
        result.append({**row, "quotes": [paragraphs[i] for i in indices]})
    return result


def gate(body, contract, report):
    expected = requirements(contract)
    if not expected:
        return []
    setup = report.get("narrative_setup") or {}
    if setup.get("protocol") != PROTOCOL:
        raise StoryError("缺少当前版本开篇动机审查")
    paragraphs = opening_paragraphs(body)
    checks = validate_checks(setup.get("checks"), expected, paragraphs)
    if any(row["quotes"] != saved.get("quotes") for row, saved in zip(checks, setup["checks"])):
        raise StoryError("开篇动机审查引文不匹配")
    return ["narrative_setup:" + row["id"] for row in checks if row["verdict"] != "supported"]
