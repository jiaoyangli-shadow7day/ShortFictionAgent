"""Scene purpose, grounded plan review, and unequal prose allocation.

These checks establish coverage and source binding. They do not turn a model's
literary judgment into a deterministic quality score.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy

from .contracts import StoryError, digest
from .chapter_structure import chapters
from . import target_reader


PROTOCOL = 'scene-purpose-and-budget-v15-explicit-expectation-coverage'
REVIEW_VIEW_PROTOCOL = 'executable-plan-view-v2-with-legacy-event-candidates'
REVIEW_PLAN_FIELDS = ('main_question', 'story_question', 'opening_story_question', 'question',
                     'core_reading_experience', 'ending_resolution', 'promises', 'continuity_map',
                     'story_facts', 'characters')
REVIEW_SCENE_FIELDS = ('scene_id', 'scene_role', 'reading_role', 'title', 'time', 'place',
                      'characters', 'goal', 'pressure', 'choice', 'consequence',
                      'budget', 'dependencies')
REVIEW_CHAIN_FIELDS = ('attempt', 'response', 'adjustment', 'change', 'next_effect')
REVIEW_BEAT_FIELDS = ('action', 'response', 'change')
REVIEW_CHAPTER_FIELDS = ('chapter_id', 'title', 'scene_ids')
REVIEW_LEGACY_SCENE_FIELDS = ('new_information', 'emotional_change')
REVIEW_LEGACY_BEAT_FIELDS = ('reward',)
READING_ROLES = {'core_experience', 'support', 'mixed'}
EXPANSION_ACTIONS = {'keep', 'compress', 'merge', 'advance', 'replace'}
PLAN_INSTRUCTION = (
    target_reader.PLAN_INSTRUCTION
    + '先根据原始contract中的标题、受众期待、reader_promise和作者前提识别本篇卖点与欲望，'
    '再设计读者想亲眼看到的戏；自拟的主问题、主题和core_reading_experience不能反过来覆盖这些承诺。'
    '成长、尊重与不确定性可以在戏中成立，不能因为主题更成熟就替换原本承诺的优势、胜利、心动或偏爱。'
    '全篇必须含main_question：用一句具体问题写清主人公要得到什么、受什么阻碍，'
    '以及读者为何急着知道结果；不能只列发财、恋爱或成长等题材方向。'
    'main_question须承接标题和导语承诺，首场建立它，后续选择持续推进，ending_resolution实际回答它。'
    '另写core_reading_experience字符串：读者为标题与题材承诺而来，主要想亲历什么具体体验与回报；'
    '它不同于主人公需要办完哪些事。每场增加reading_role=core_experience、support或mixed，'
    '分别表示直接提供核心阅读体验、主要完成支撑任务、两者兼有；scene_role仍表示核心戏或过渡，二者不可混同。'
    '商业交锋若有方法、选择、代价和实际回应，也能直接提供核心体验；安静偏爱和主动选择也能构成实质回报，'
    '不能把一切手续都叫核心体验，也不能把所有商业戏或静谧戏都判无价值。'
    '区分剧情场景、章节和模型输出分块：一章可包含多个有效节拍，分块不等于增加一场戏。'
    '全篇必须含chapters数组，每项chapter_id、title、scene_ids；按实际场景顺序连续分组，'
    '每个scene_id恰好归属一章，章节ID唯一、标题非空。一章可包含多场，按完整交锋及阅读回报自然分章，'
    '首章须完成一个实际交锋，不能用模型输出长度切章，也不能把全篇都装进“第一章”来逃过逐章审读。'
    '每场增加scene_role=core或transition；核心场景增加dramatic_chain对象，'
    'attempt、response、adjustment、change、next_effect、deletion_loss六个非空字符串字段必须全部嵌套在该对象内。'
    '结构示意（值须改成实际剧情）：{"scene_role":"core","dramatic_chain":'
    '{"attempt":"具体行动","response":"实际接招","adjustment":"因此调整",'
    '"change":"实际改变","next_effect":"后续作用或本场闭合","deletion_loss":"具体删场损失"}}。'
    '场景根级的deletion_loss不能替代dramatic_chain.deletion_loss；其他链条字段也不能移出该对象。'
    'attempt写人物为当前目标采取的具体行动，response写对方实际接招或环境反馈，'
    'adjustment写人物因此怎样调整，change写本场实际改变，next_effect写它怎样影响后续选择；'
    '若价值已在本场闭合则如实说明，不补造后续作用。'
    'deletion_loss写删掉本场会失去的具体后续因果、情感递进，或本场已实际兑现的欲望、身份、生活选择或主题体验。'
    '不能仅以是否改变最后恋爱或主任务结果衡量所有场景。'
    '全篇增加premise_test字符串：分别检查标题中各个主要卖点改变了哪些选择与后果，'
    '不能把整组设定一起删除、只凭开头一个收益就证明后续全部成立。'
    '有限能力还要保留已有身份与已兑现结果，只移除剩余能力，比较后续策略、胜负或关系发展是否仍原样成立。'
    '承诺提供优势时写出实际收益及它引起的下一次选择，限制与查不到答案不能独自算作优势兑现；'
    '不要求用满次数，不凭空添加能力，也不为通过检查虚构额外事件。'
    '核心戏不能只写“更信任、更心动、通过考验”；需交代可落笔的动作与回应。'
    '先想本章读者正等哪次交锋、得利、反击或亲近，再构思主人公主动争取、对方有自身欲望的接招和实际回报，'
    '用现有progression_beats把这些戏落下来，再按真正不同的选择与局面变化分场；'
    '不要按年份、比赛或职业里程碑排时间表后才补感情。人物愿望、好奇、试探、竞争和偏爱都可形成张力，'
    '不必每场让主角犯错再被教育，也不能把一次关系道理换场景重复学习。'
    '作者禁止项用于筛除方案，不要逐条编成主角先犯被禁止错误、再被纠正的剧情来证明遵守；'
    '先从主人公及对方的主动欲望、互相需要、竞争或偏爱与不同回报设计戏，不强迫女主犯错或添加反派。'
    '每个progression_beat要写出具体response，区分人物自己的下一动作和对方实际回应；'
    'action、response、change和consequence须完整写出角色实际得到什么、失去什么或怎样改变局面；'
    'reward只写角色实际所得，不能用“读者看到”“设定生效”“避免沦为装饰”等自评替代剧情。'
    '关键误会、表态、竞争或亲密转折须提前规划触发与接招，不能等正文靠心理或漂亮对白补足。'
    '安静互动、主动偏爱和共同选择同样有效，不强加事故、背叛、反转或道德惩罚；'
    '过渡可只有地点时间衔接或必要信息，用短篇幅完成，不必伪装成核心交锋。'
    'budget必须为正整数，表示该场正文目标可见字符数；按戏的重要性分配，'
    '预算合计接近正文目标，准备、手续、演练、复述不得因均分而扩写。'
    '承认本场已兑现的价值不等于当前预算合理；仍比较可见行动、实际接招、调整与回报能支撑多少展开，'
    '以及压缩后具体保住和失去什么。'
    '规划时从整篇比较各段值得展开多少：连续相似任务能否压缩、合并或换场，'
    '第一个实质回报以前读者实际要等哪些场景、在回报所在场景内还要经过哪些铺垫和交锋；'
    '回报安排在首场不代表零等待，不能把一场几千字内的准备过程藏进场景编号。'
    '若提前兑现部分体验，会失去什么、又赢得什么。'
    '比较压缩方案时保留相同的必要行动、实际接招和后果，尝试合并重复验证、解释和同功能互动；'
    '不能把原方案与删光因果、只宣布结果的摘要比较，借替代方案更空泛来证明全部展开必要。'
    '实质回报必须已发生，不能把“以后会发财/相爱”的预告算兑现；不指定早吻、固定百分比或冲突次数。'
    '明确的作者场数要求仍须遵守；只有增加新的作用才保留多个相似任务。'
    '以上字段用于作者内部执行，不能作为解释写进正文。'
)
REVIEW_INSTRUCTION = (
    target_reader.REVIEW_INSTRUCTION
    + '先对照原始contract及target_reader_context识别读者为什么点进本篇，不能仅凭方案自拟的'
    'core_reading_experience自证已经兑现。作者承诺的优势、胜利、心动或偏爱被替换成成熟相处主题时，'
    '即使人物健康、做事合理、有成长，也须指出实际落空的体验。'
    '番茄渠道先输出premise_test={status:active/ordinary/needs_replan,checks:[...]}，再作逐场与预算检查。'
    'checks按原始入口的独立主要卖点逐项审读，不把不同能力、身份和恋爱回报合成一个“有用”；'
    'contract.audience.expectations每项非空原始期待都必须至少有一项check精确引用'
    'contract.audience.expectations[n]，不能只引用author_input.entry_facts等较弱事实绕开它。'
    '这只是最低覆盖：同一原字段中的不同能力或收益仍要分别检查，可使用相同entry_path和不同的'
    '真实entry_quote；title、reader_promise和其他入口可附加，不用字段条数代替独立卖点判断。'
    '每项含entry_path、entry_quote、expected_payoff、actual_payoff、remaining_effect、without_remaining、'
    'status(fulfilled/needs_replan)、evidence。entry_path指向原始contract的title、reader_promise、'
    'audience.expectations[n]、author_input.premise/entry_facts[n]或genre.custom_description；'
    'entry_quote逐字引用该字段中的本项卖点，不能引用plan的自拟主问题或作者禁止项。'
    'expected_payoff写读者想亲历的具体欲望收益；actual_payoff据现有行动、接招和后果写实际得到了什么，'
    '未得到则直说缺失，不能把“有成长/有因果作用”当收益。evidence每项scene_id和quote，绑定实际可见场景。'
    'remaining_effect=material/boundary_only/no_later_effect/not_applicable；without_remaining保留既得收益、'
    '身份和已形成关系，只移除余下能力或未发挥的卖点，比较之后的赢法、可选行动、所得和关系是否真变。'
    'material指具体策略或回报发生实质差异；boundary_only指只验证限制或形成认知主题；'
    'no_later_effect指后续原样成立；一次性承诺或无剩余机制可not_applicable。'
    '这些分类本身不决定好坏：是否充分须对照该项原始承诺，不能把一次实际收益加一次边界说明'
    '自动视为整项优势已兑现，也不能用别项圆满抵扣；明确的一次性收益、温情或恋爱偏爱可充分兑现。'
    '逐项判断原始承诺是否匹配实际回报；缺口才needs_replan。任一项needs_replan须整体needs_replan并给真实修复路径。'
    '其他渠道仍可用原premise_test的status、reason、without_setting_changes和场景evidence，不强套番茄卖点审法。'
    'main_question_check对象含status=effective或needs_replan，question_quote逐字引用main_question，'
    'opening_scene_id与ending_scene_id引用实际场景ID，ending_quote逐字引用ending_resolution，reason说明'
    '开场怎样建立这个具体期待、关键选择怎样接续、结局怎样回答；题材方向不能冒充主问题。'
    '若主问题宽泛、导语承诺与剧情分离或中段偏离，在真实方案字段上报告issues并needs_replan。'
    '同一次主审必须核对每个场景，输出scene_checks数组，恰好覆盖全部scene_id一次。'
    '每项含scene_id、kind(core/transition)、status(effective/needs_replan)、'
    'attempt_quote、response_quote、change_quote（逐字引用该场真实字符串内容）、'
    'next_scene_effect、loss_if_removed、reason。'
    'core必须核对目标行动、实际接招及局面/理解/关系变化；缺失的引文用空字符串并needs_replan。'
    'plan是独立审读投影，保留完整动作、回应、变化与预算。legacy_material单列旧字段候选材料，'
    '每项source_path指向原方案字段，text是原文；其中真实事件或人物所得须据实审查，不能因放在旧字段就漏掉。'
    '候选材料也可能只是“读者会喜欢/设定已生效”的策划自评，不得以这些宣称直接证明价值，'
    '也不能把未发生的预告当成已实现的结果；源引文能定位不等于自评属实。'
    'core_reading_experience、promises及结构标签只是待检的作者声明，不能作为已兑现的证据。'
    '逐场阅读progression_beats中的action、response、change及具体后果，独立推断眼下期待与回报；'
    '不能仅凭dramatic_chain概括完整就放行，不补造投影中没有的动作或结果。'
    '核对大纲究竟已经想出了具体的戏，还是只写毕业、比赛、合作等节点和待正文补写的结果。'
    '故事规划的吸引力与字段齐全分开判断：角色名、年份、资源或名次变了，不自动产生值得等待的新问题。'
    '按chapters逐章核对实际场景预算合计、完整交锋、阅读点和章末承接；适用指南的篇幅建议按稿型使用。'
    '若方案有显式chapters，同一次主审必须输出chapter_checks数组，恰好覆盖每个实际chapter_id一次；'
    'chapter_budget_context由程序按真实scene_ids计算planned_budget，直接据此评估，不需要重复计算或输出预算。'
    '每项仅含chapter_id、status(effective/needs_replan)、reason和非空evidence数组；'
    'reason具体说明读者想亲眼看到的那次戏是否已设计出主动争取、实际接招与回报，'
    '完整交锋为何分在这一章、预算是否在展开不同阅读价值还是重复步骤或同一道理、'
    '相对前文新增什么回报，以及章末如何续接或在结尾收束。长章必要时可保留，安静偏爱也可构成价值；'
    '不能以切章或贴标签代替新剧情，不把1000字当全部中短篇硬阈值，也不强迫每章新事故。'
    'evidence每项含scene_id、beat_index、quote，scene_id必须属于当前章，beat_index为该场实际'
    'progression_beats从0开始的整数位置，quote逐字引用该节拍；仅旧方案该场没有节拍时可用'
    'beat_index=null并逐字引用该场已有内容。证据只证明判断可定位，不证明吸引力。'
    '任一章needs_replan须decision=revise，并在issues指出该章chapters或所含scenes的真实修改路径；'
    '同一跨章结构问题可用真实父数组路径plan.chapters或plan.scenes集中回修，不必逐章重复报同一issue。'
    '不能一边称章预算或重复功能要重排，一边pass。无显式chapters的旧方案不补造章图，'
    'chapter_checks可省略或为空，仍需原有逐场和全篇审查。'
    'reading_points的标签和引用齐全只证明可定位，不能证明已设计出有吸引力的剧情。'
    '若长章藏了多个独立戏或重复验证，在真实chapters或scenes路径要求重排；'
    '若短章只有时间地点衔接或空悬念，也不能仅因字数达标放行。'
    '把人物从不主动争取写成配合、反复犯同一错再学同一道理，若偏离作者欲望和题材回报，须回到剧情规划修。'
    'transition只需attempt_quote锚定该场实际用途，另两引文可空，但必须说明为何必要且应短写。'
    '这些字段是检查结果，不是帮方案补写缺失情节；不得用未来打算证明本场已改变。'
    '把同一难题、同一接招、同一解决办法重复一遍，只换客户、地点、验收身份，须标needs_replan；'
    '也检查跨场的阅读功能是否重复：多次证明同一能力、多次学会同一关系道理、多次确认已经确认的偏爱，'
    '即使事件不同、每场都能找出新增事实，也不能自动当成新的吸引力；须指出新增的期待或回报，无法指出则合并或换场。'
    '核对删场会失去的具体后续因果、情感递进，或本场已实际兑现的欲望、身份、生活选择或主题体验；'
    '不能仅以是否改变最后恋爱或主任务结果衡量所有场景，不能以“丰富细节”当保留理由。'
    '若价值已在本场闭合，next_scene_effect如实说明，不补造后续作用。'
    '商业交锋若有方法、选择、代价和实际回应，也能直接提供核心体验；安静偏爱和主动选择也能构成实质回报。'
    '安静但实际回应、偏爱、试探和共同选择可以effective，不以吵架、伤害或反转次数判断。'
    '另输出reading_allocation_check对象，独立于逐场因果证明：status=effective或needs_replan，'
    'core_experience_ref填写固定源字段路径plan.core_reading_experience；旧方案没有该字段时填写'
    '当前真实主问题字段路径，如plan.main_question或plan.story_question，作为待审期待；不用重抄长原文。'
    '兼容旧core_experience_quote时仍须逐字准确，不得替旧方案编造已经存在的回报。'
    'total_budget为全部实际scene.budget之和。'
    'first_reward含scene_id、quote、budget_before、within_scene_wait、reason、alternative、action：'
    '指出第一个已发生的实质阅读回报，quote逐字引用该场，budget_before为该场以前所有场景预算之和，'
    '它只表示前置场景预算，不是实际等待。within_scene_wait必须为对象，含before_reward、estimate_chars、basis：'
    'before_reward具体列出本场回报以前的准备、交锋和重复验证；estimate_chars为其计划可见字符估计（0至本场budget），'
    '无法从方案判断则填null并在basis说明缺少什么，不能编造精确字数；basis说明估计依据与不确定性。'
    '即使scene_id是首场、budget_before=0，仍须检查场内等待；只有回报在开场直接发生才能估为0。'
    'reason解释这为何已经兑现期待，alternative具体比较把什么体验提前或换场会怎样，'
    '结合前置场景与场内过程判断等待是否值得，不能用“第一场就兑现”代替判断。'
    'action=keep、advance或replace；没有实质回报则scene_id=null、quote为空、budget_before=total_budget、'
    'within_scene_wait.estimate_chars=null并needs_replan，另两字段说明无回报的实际过程。'
    'expansion_choices数组按实际顺序将全部场景恰好覆盖一次，每项含连续的scene_ids、'
    'reading_role(core_experience/support/mixed)、planned_budget（该组实际预算之和）、'
    'action(keep/compress/merge/advance/replace)、alternative、compression_trial、preserved_value、lost_value、reason。'
    '组内已有reading_role一致则照录，有不同角色或含mixed则标mixed；旧方案无标注时根据原方案判断，不能补剧情。'
    'alternative必须给具体保留/压缩/合并/提前/换场对照，preserved_value与lost_value比较因此保住和失去的阅读价值，'
    '每组compression_trial必须含retained_beats、compressed_route、removable_repetition。'
    'retained_beats数组按本组顺序覆盖每场一次，每项scene_id、action_quote、response_quote；'
    '引文来自实际场景，核心场的行动和接招均须保留，过渡的response_quote可空。'
    'compressed_route写可落笔的缩短路线，保留这些必要行动、接招及同一结果，不增加新剧情，'
    '不能只写“他考了第一/她更信任他”或删掉全部交锋后再说摘要不如原稿。'
    'removable_repetition指出可合并或删去的重复证明、解释、心理复述或同功能互动；'
    '若确实没有，说明各必要节拍分别提供什么不同回报。keep也必须经过此对照，不能跳过压缩试验。'
    '不能只写“有因果作用所以保留”。连续相似任务须合在一组比较，merge至少涉及两场。'
    '承认本场已兑现的价值不等于当前预算合理；仍比较可见行动、实际接招、调整与回报能支撑多少展开，'
    '以及压缩后具体保住和失去什么。'
    'sequence_comparison具体检查连续同类办事是否只换对象重复，若没有则指出实际任务/回应的区别；'
    'whole_story_tradeoff对照整篇预算与标题承诺，说明哪些值得展开、哪些只需交代，以及首个回报的等待是否值得。'
    '不按固定比例、早吻或反转数量裁决，不把一切资金、权利细节机械删除；判断取决于该篇真实阅读体验。'
    '任何非keep取舍表示当前方案还需改动，须needs_replan并在issues列真实路径；不能一边要求压缩合并一边pass。'
    '任何needs_replan必须在issues中给出plan内真实可修改路径、reason、missing_link，并decision=revise。'
    'scene_checks、premise_test和reading_allocation_check补充同一份主审结果，不新增角色、独立逐场调用或评分。'
)


def criteria_hash() -> str:
    return digest({'protocol': PROTOCOL, 'plan': PLAN_INSTRUCTION, 'review': REVIEW_INSTRUCTION,
                   'review_view': {'protocol': REVIEW_VIEW_PROTOCOL,
                       'plan_fields': REVIEW_PLAN_FIELDS, 'scene_fields': REVIEW_SCENE_FIELDS,
                       'chain_fields': REVIEW_CHAIN_FIELDS, 'beat_fields': REVIEW_BEAT_FIELDS,
                       'chapter_fields': REVIEW_CHAPTER_FIELDS,
                       'legacy_scene_fields': REVIEW_LEGACY_SCENE_FIELDS,
                       'legacy_beat_fields': REVIEW_LEGACY_BEAT_FIELDS}})


def review_view(plan: dict) -> dict:
    """Project executable events, not the planner's claims about their quality.

    This is a field contract, not a keyword classifier. Strings in the selected
    action fields remain complete, even if characters discuss a rule or a
    boundary. The source plan and writer inputs are never rewritten here.
    """
    def selected(value, fields):
        return {key: deepcopy(value[key]) for key in fields if key in value}

    result = selected(plan, REVIEW_PLAN_FIELDS)
    result['scenes'] = []
    for scene_index, scene in enumerate(plan['scenes']):
        row = selected(scene, REVIEW_SCENE_FIELDS)
        legacy = [{'source_path': f'plan.scenes[{scene_index}].{key}', 'text': deepcopy(scene[key])}
                  for key in REVIEW_LEGACY_SCENE_FIELDS if key in scene]
        if legacy:
            row['legacy_material'] = legacy
        if isinstance(scene.get('dramatic_chain'), dict):
            row['dramatic_chain'] = selected(scene['dramatic_chain'], REVIEW_CHAIN_FIELDS)
        if 'progression_beats' in scene:
            row['progression_beats'] = []
            for beat_index, beat in enumerate(scene['progression_beats']):
                projected = selected(beat, REVIEW_BEAT_FIELDS)
                legacy = [{'source_path': f'plan.scenes[{scene_index}].progression_beats[{beat_index}].{key}',
                           'text': deepcopy(beat[key])} for key in REVIEW_LEGACY_BEAT_FIELDS if key in beat]
                if legacy:
                    projected['legacy_material'] = legacy
                row['progression_beats'].append(projected)
        result['scenes'].append(row)
    if 'chapters' in plan:
        result['chapters'] = [selected(chapter, REVIEW_CHAPTER_FIELDS)
                              for chapter in plan['chapters']]
    return result


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == 'legacy_material' and isinstance(item, list):
                # Source paths are provenance metadata, never story quotes.
                for candidate in item:
                    if isinstance(candidate, dict):
                        yield from _strings(candidate.get('text'))
            else:
                yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _quote_matches(quote, scene):
    source = {key: value for key, value in scene.items()
              if key not in {'scene_id', 'scene_role', 'reading_role', 'dependencies', 'budget'}}
    return isinstance(quote, str) and len(quote.strip()) >= 2 and any(
        quote in text for text in _strings(source))


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _text_or_lines(value):
    """Accept prose or explicit prose lines without changing the source reply."""
    return _nonempty(value) or (isinstance(value, list) and bool(value)
                               and all(_nonempty(item) for item in value))


def _structured_review_text(value):
    """A structured comparison is equivalent prose, not a new pass decision.

    Status-only objects and malformed metadata cannot substitute for the actual
    comparison. Keep the original object intact so repairs see its full finding.
    """
    if _text_or_lines(value):
        return True
    if (not isinstance(value, dict) or not value
            or any(not _nonempty(key) for key in value)):
        return False
    if 'status' in value and (not isinstance(value['status'], str)
                             or value['status'] not in {'effective', 'needs_replan'}):
        return False
    content = [item for key, item in value.items() if key != 'status']
    return bool(content) and all(_structured_review_text(item) for item in content)


def _contains_replan_status(value):
    if isinstance(value, dict):
        return (value.get('status') == 'needs_replan'
                or any(_contains_replan_status(item) for item in value.values()))
    if isinstance(value, list):
        return any(_contains_replan_status(item) for item in value)
    return False


def _resolve_plan_path(path, plan):
    """Resolve numeric and real ID selectors to one canonical source path.

    Selectors are data lookups, not expressions. Unknown or ambiguous IDs never
    fall back to a numeric position. Both existence and chapter ownership use
    these same resolved keys, so two spellings cannot disagree about scope.
    """
    if not isinstance(path, str):
        return None
    path = path.removeprefix('plan.')
    first = re.match(r'[A-Za-z_][A-Za-z_0-9]*', path)
    if first is None:
        return None
    try:
        key = first.group()
        if not isinstance(plan, dict):
            return None
        current, resolved, position = plan[key], [key], first.end()
        while position < len(path):
            token = re.match(
                r'\.(?P<key>[A-Za-z_][A-Za-z_0-9]*)'
                r'|\[(?P<index>\d+)\]'
                r'|\[(?P<id_field>scene_id|chapter_id)=(?P<id_value>[^\[\]\r\n]+)\]',
                path[position:])
            if token is None:
                return None
            if token['key'] is not None:
                if not isinstance(current, dict):
                    return None
                key = token['key']
            else:
                if not isinstance(current, list):
                    return None
                if token['index'] is not None:
                    key = int(token['index'])
                else:
                    matches = [index for index, row in enumerate(current)
                               if isinstance(row, dict)
                               and row.get(token['id_field']) == token['id_value']]
                    if len(matches) != 1:
                        return None
                    key = matches[0]
            current = current[key]
            resolved.append(key)
            position += token.end()
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return tuple(resolved)


def _path_exists(path, plan):
    return _resolve_plan_path(path, plan) is not None


def _path_targets_chapter(path, plan, chapter_index, scene_indices):
    resolved = _resolve_plan_path(path, plan)
    if resolved is None:
        return False
    if resolved in {('chapters',), ('scenes',)}:
        return True
    return len(resolved) >= 2 and (
        resolved[0] == 'chapters' and resolved[1] == chapter_index
        or resolved[0] == 'scenes' and resolved[1] in scene_indices)


def _compression_trial(row: dict, scenes: list[dict]) -> None:
    """Require a grounded alternative, not a fabricated result-only foil.

    These bindings make the alternative inspectable. Whether the shorter route
    is compelling remains the editor's judgment, rather than a string heuristic.
    """
    trial = row.get('compression_trial')
    if (not isinstance(trial, dict)
            or any(not _nonempty(trial.get(key)) for key in
                   ('compressed_route', 'removable_repetition'))):
        raise StoryError('全篇展开取舍缺少保留必要交锋的压缩试验')
    beats = trial.get('retained_beats')
    if (not isinstance(beats, list) or any(not isinstance(beat, dict) for beat in beats)
            or [beat.get('scene_id') for beat in beats] != [scene['scene_id'] for scene in scenes]):
        raise StoryError('压缩试验须按本组顺序覆盖每个实际场景的必要行动和回应')
    for beat, scene in zip(beats, scenes):
        if not _quote_matches(beat.get('action_quote'), scene):
            raise StoryError('压缩试验缺少对应场景的实际行动引文')
        response = beat.get('response_quote')
        if (not isinstance(response, str)
                or (response and not _quote_matches(response, scene))
                or (scene.get('scene_role') != 'transition' and not response)):
            raise StoryError('压缩试验不能删掉核心交锋或编造对应场景的实际回应')


def _reading_allocation_check(response: dict, plan: dict, *, evidence_plan=None) -> dict:
    """Bind literary comparisons to the real route, without scoring their taste.

    Old plans may be reviewed using their explicit main question. Old review
    receipts are not migrated into a pass: they must contain this new review.
    """
    ordered = plan['scenes']
    evidence_ordered = (evidence_plan or plan)['scenes']
    ids = [scene['scene_id'] for scene in ordered]
    if len(set(ids)) != len(ids):
        raise StoryError('全篇取舍审查的实际scene_id重复')
    budgets = validate_budgets(plan, {})
    total = sum(budgets)
    check = response.get('reading_allocation_check')
    experience = plan.get('core_reading_experience')
    if 'core_reading_experience' not in plan:
        experience = main_question(plan, required=True)
    if not _nonempty(experience):
        raise StoryError('方案core_reading_experience必须明确核心阅读体验')
    if 'core_reading_experience' in plan:
        experience_ref = 'plan.core_reading_experience'
    else:
        experience_ref = next('plan.' + key for key in
            ('main_question', 'story_question', 'opening_story_question', 'question')
            if _nonempty(plan.get(key)))
    if (not isinstance(check, dict)
            or not isinstance(check.get('status'), str)
            or check['status'] not in {'effective', 'needs_replan'}
            or not any(key in check for key in ('core_experience_quote', 'core_experience_ref'))
            or ('core_experience_quote' in check and check['core_experience_quote'] != experience)
            or ('core_experience_ref' in check and check['core_experience_ref'] != experience_ref)
            or type(check.get('total_budget')) is not int
            or check['total_budget'] != total
            or any(not _structured_review_text(check.get(key)) for key in
                   ('sequence_comparison', 'whole_story_tradeoff'))):
        raise StoryError('主审缺少绑定实际预算与核心体验的全篇展开取舍审查')

    choices = check.get('expansion_choices')
    if not isinstance(choices, list) or not choices:
        raise StoryError('全篇展开取舍缺少实际场景覆盖')
    covered = []
    needs_change = any(_contains_replan_status(check[key]) for key in
                       ('sequence_comparison', 'whole_story_tradeoff'))
    for row in choices:
        if (not isinstance(row, dict)
                or not isinstance(row.get('scene_ids'), list) or not row['scene_ids']
                or any(not isinstance(key, str) for key in row['scene_ids'])
                or not isinstance(row.get('reading_role'), str)
                or row['reading_role'] not in READING_ROLES
                or not isinstance(row.get('action'), str)
                or row['action'] not in EXPANSION_ACTIONS
                or type(row.get('planned_budget')) is not int
                or any(not _nonempty(row.get(key)) for key in
                       ('alternative', 'preserved_value', 'lost_value', 'reason'))):
            raise StoryError('全篇展开取舍缺少具体对照、阅读得失或有效动作')
        start = len(covered)
        keys = row['scene_ids']
        # This is an ordered partition: no fabricated IDs, gaps, duplicate
        # scenes, or distant tasks grouped to hide what happens between them.
        if keys != ids[start:start + len(keys)]:
            raise StoryError('全篇展开取舍必须按实际顺序连续覆盖全部scene_id一次')
        if row['planned_budget'] != sum(budgets[start:start + len(keys)]):
            raise StoryError('全篇展开取舍预算不等于对应实际场景预算')
        known_roles = [scene.get('reading_role') for scene in ordered[start:start + len(keys)]]
        if all(role is not None for role in known_roles):
            expected_role = known_roles[0] if len(set(known_roles)) == 1 else 'mixed'
            if row['reading_role'] != expected_role:
                raise StoryError('全篇展开取舍的阅读角色不属于对应方案标注')
        if row['action'] == 'merge' and len(keys) < 2:
            raise StoryError('全篇合并取舍必须比较至少两个实际场景')
        _compression_trial(row, evidence_ordered[start:start + len(keys)])
        needs_change |= row['action'] != 'keep'
        covered.extend(keys)
    if covered != ids:
        raise StoryError('全篇展开取舍必须覆盖全部实际场景')

    reward = check.get('first_reward')
    if (not isinstance(reward, dict)
            or 'scene_id' not in reward
            or not isinstance(reward.get('action'), str)
            or reward['action'] not in {'keep', 'advance', 'replace'}
            or type(reward.get('budget_before')) is not int
            or any(not _nonempty(reward.get(key)) for key in ('reason', 'alternative'))):
        raise StoryError('全篇取舍缺少首个实质回报的位置与提前对照')
    reward_id = reward.get('scene_id')
    if reward_id is None:
        if reward.get('quote') != '' or reward['budget_before'] != total:
            raise StoryError('未发生实质回报时须明确全篇等待预算，不能补造证据')
        needs_change = True
    else:
        if (not isinstance(reward_id, str) or reward_id not in ids
                or not _quote_matches(reward.get('quote'), evidence_ordered[ids.index(reward_id)])):
            raise StoryError('首个实质回报引用不属于对应实际场景')
        if reward['budget_before'] != sum(budgets[:ids.index(reward_id)]):
            raise StoryError('首个实质回报之前的预算不等于实际场景累计预算')
    wait = reward.get('within_scene_wait')
    if (not isinstance(wait, dict) or 'estimate_chars' not in wait
            or not _text_or_lines(wait.get('before_reward'))
            or not _nonempty(wait.get('basis'))):
        raise StoryError('首个实质回报须检查场内等待，前置场景预算不能冒充实际等待')
    estimate = wait['estimate_chars']
    if (estimate is not None and (type(estimate) is not int or estimate < 0
            or reward_id is None or estimate > budgets[ids.index(reward_id)])):
        raise StoryError('首个回报的场内等待估计须在本场预算内；未知或无回报填null')
    needs_change |= reward['action'] != 'keep'
    if needs_change and check['status'] != 'needs_replan':
        raise StoryError('全篇取舍要求改动或缺少回报，却仍声称方案有效')
    return check


def chapter_budget_context(plan: dict) -> list[dict]:
    """Compute explicit chapter budgets without inventing a legacy chapter map.

    These are authored scene targets, not a per-chapter length limit or a
    quality score. The editor decides whether each complete exchange earns its
    planned room. Returned rows are fresh and cannot mutate the source plan.
    """
    if 'chapters' not in plan:
        return []
    units = chapters(plan, allow_legacy=False)
    budgets = validate_budgets(plan, {})
    return [{'chapter_id': unit['chapter_id'], 'scene_ids': list(unit['scene_ids']),
             'planned_budget': sum(budgets[index] for index in unit['scene_indices'])}
            for unit in units]


def _chapter_checks(response: dict, plan: dict, *, evidence_plan=None) -> bool:
    """Require a source-bound observation for every explicitly planned chapter.

    Coverage cannot certify that an editor's literary judgment is correct.
    Quiet or long chapters are allowed; negative judgments must reach the
    existing structural repair route rather than disappearing after checking.
    """
    context = chapter_budget_context(plan)
    checks = response.get('chapter_checks')
    if not context:
        if checks is not None and checks != []:
            raise StoryError('旧方案无显式chapters，主审不能补造章节检查')
        return False
    ids = [unit['chapter_id'] for unit in context]
    if (not isinstance(checks, list) or len(checks) != len(ids)
            or any(not isinstance(row, dict) or not isinstance(row.get('chapter_id'), str)
                   for row in checks)
            or len({row['chapter_id'] for row in checks}) != len(checks)
            or {row['chapter_id'] for row in checks} != set(ids)):
        raise StoryError('主审缺少逐章预算与分章审查，须恰好覆盖每个实际chapter_id一次')
    by_id = {unit['chapter_id']: unit for unit in context}
    scenes = {scene['scene_id']: scene for scene in (evidence_plan or plan)['scenes']}
    scene_positions = {scene['scene_id']: index for index, scene in enumerate(plan['scenes'])}
    needs_replan = False
    for row in checks:
        if (not isinstance(row.get('status'), str)
                or row['status'] not in {'effective', 'needs_replan'}
                or not _nonempty(row.get('reason'))):
            raise StoryError('主审逐章审查缺少具体分章、预算和重复功能理由')
        unit = by_id[row['chapter_id']]
        evidence = row.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise StoryError('主审逐章审查缺少本章真实节拍证据')
        for item in evidence:
            if (not isinstance(item, dict) or not isinstance(item.get('scene_id'), str)
                    or item['scene_id'] not in unit['scene_ids'] or 'beat_index' not in item):
                raise StoryError('主审逐章证据必须属于当前章实际场景，不能跨章或补造引用')
            scene = scenes[item['scene_id']]
            beats, index = scene.get('progression_beats'), item['beat_index']
            if beats:
                if (not isinstance(beats, list) or type(index) is not int
                        or not 0 <= index < len(beats) or not isinstance(beats[index], dict)
                        or not _quote_matches(item.get('quote'), beats[index])):
                    raise StoryError('主审逐章证据不属于对应实际progression_beats节拍')
            elif index is not None or not _quote_matches(item.get('quote'), scene):
                raise StoryError('旧场景无节拍时逐章证据须beat_index=null并引用本场已有内容')
        if row['status'] == 'needs_replan':
            needs_replan = True
            chapter_index = ids.index(row['chapter_id'])
            scene_indices = {scene_positions[key] for key in unit['scene_ids']}
            issues = response.get('issues')
            if not isinstance(issues, list) or not any(
                    isinstance(issue, dict) and _path_targets_chapter(
                        issue.get('path'), plan, chapter_index, scene_indices)
                    for issue in issues):
                raise StoryError('主审逐章发现结构缺口却未在本章真实chapters或scenes路径退回规划')
    return needs_replan


def _entry_quote_matches(path, quote, contract):
    """Only positive original-entry fields may ground an independent sellpoint."""
    if not _nonempty(path) or not _nonempty(quote) or path.startswith('plan.'):
        return False
    resolved = _resolve_plan_path(path.removeprefix('contract.'), contract)
    if resolved is None:
        return False
    scalar = {('title',), ('reader_promise',), ('author_input', 'premise'),
              ('genre', 'custom_description')}
    listed = {('audience', 'expectations'), ('author_input', 'entry_facts')}
    if resolved not in scalar and not (len(resolved) == 3 and resolved[:2] in listed
                                       and type(resolved[2]) is int):
        return False
    value = contract
    for key in resolved:
        value = value[key]
    return (isinstance(value, str) and quote in value
            and (len(quote.strip()) >= 2 or
                 (resolved[:2] == ('audience', 'expectations') and quote == value)))


def _premise_review(premise, scenes, contract):
    """Bind separate entry promises and their observations, not literary taste.

    A boundary-only effect is not a programmatic rejection. The editor must
    judge what that original promise offered; only its negative judgment routes
    repair. Existing callers without a contract keep the old source-only API.
    """
    if (not isinstance(premise, dict)
            or premise.get('status') not in {'active', 'ordinary', 'needs_replan'}):
        raise StoryError('主审缺少去设定后的选择与后果核验')
    required = bool(contract and 'fanqie_short' in (contract.get('channels') or []))
    checks = premise.get('checks')
    if not required and checks is None:
        if any(not _nonempty(premise.get(key)) for key in ('reason', 'without_setting_changes')):
            raise StoryError('主审缺少去设定后的选择与后果核验')
        observations = [premise]
    else:
        if not isinstance(checks, list) or not checks:
            raise StoryError('主审缺少按原始独立卖点分别核验的premise_test.checks')
        observations = checks
        for row in checks:
            if (not isinstance(row, dict) or row.get('status') not in {'fulfilled', 'needs_replan'}
                    or row.get('remaining_effect') not in
                    {'material', 'boundary_only', 'no_later_effect', 'not_applicable'}
                    or any(not _nonempty(row.get(key)) for key in
                           ('entry_path', 'entry_quote', 'expected_payoff', 'actual_payoff', 'without_remaining'))):
                raise StoryError('主审独立卖点缺少原始期待、实际收益或保留既得收益的后续对照')
            if contract is not None and not _entry_quote_matches(row['entry_path'], row['entry_quote'], contract):
                raise StoryError('主审卖点引文不属于原始合同入口，不能用方案自评或禁止项替代')
        if required:
            audience = contract.get('audience') or {}
            expectations = audience.get('expectations') if isinstance(audience, dict) else None
            expected_paths = {
                f'contract.audience.expectations[{index}]'
                for index, value in enumerate(expectations if isinstance(expectations, list) else [])
                if _nonempty(value)}
            missing = expected_paths - {row['entry_path'] for row in checks}
            if missing:
                raise StoryError('主审未覆盖每项非空原始读者期待：' + '、'.join(sorted(missing)))
        if any(row['status'] == 'needs_replan' for row in checks) and premise['status'] != 'needs_replan':
            raise StoryError('主审独立卖点发现缺口却声称整体设定已生效')
    for row in observations:
        evidence = row.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise StoryError('主审设定作用缺少实际场景证据')
        for item in evidence:
            if (not isinstance(item, dict) or item.get('scene_id') not in scenes
                    or not _quote_matches(item.get('quote'), scenes[item['scene_id']])):
                raise StoryError('主审设定作用引用不属于对应场景')


def validate_review(response: dict, plan: dict, *, evidence_plan=None, contract=None) -> dict:
    """Reject an ungrounded or incomplete pass; do not infer missing judgments."""
    scenes = {str(scene['scene_id']): scene for scene in (evidence_plan or plan)['scenes']}
    question = main_question(plan, required=True)
    check = response.get('main_question_check')
    if (not isinstance(check, dict) or check.get('status') not in {'effective', 'needs_replan'}
            or check.get('question_quote') != question
            or check.get('opening_scene_id') != plan['scenes'][0]['scene_id']
            or check.get('ending_scene_id') != plan['scenes'][-1]['scene_id']
            or not _nonempty(check.get('ending_quote'))
            or check['ending_quote'] not in str(plan.get('ending_resolution') or '')
            or not _nonempty(check.get('reason'))):
        raise StoryError('主审缺少真实主问题从开场到结局的期待核验')
    checks = response.get('scene_checks')
    if (not isinstance(checks, list) or len(checks) != len(scenes)
            or any(not isinstance(row, dict) for row in checks)
            or len({row.get('scene_id') for row in checks}) != len(checks)
            or {row.get('scene_id') for row in checks} != set(scenes)):
        raise StoryError('主审大纲缺少逐场用途与变化证据；不能视为通过')
    for row in checks:
        if (row.get('kind') not in {'core', 'transition'}
                or row.get('status') not in {'effective', 'needs_replan'}
                or any(not _nonempty(row.get(key)) for key in
                       ('next_scene_effect', 'loss_if_removed', 'reason'))):
            raise StoryError('主审逐场用途判断无效')
        scene = scenes[row['scene_id']]
        for field in ('attempt_quote', 'response_quote', 'change_quote'):
            quote = row.get(field)
            if not isinstance(quote, str) or (quote and not _quote_matches(quote, scene)):
                raise StoryError('主审场景变化引用不属于对应场景')
        if not _quote_matches(row.get('attempt_quote'), scene):
            raise StoryError('主审场景用途缺少真实方案引文')
        if row['kind'] == 'core' and row['status'] == 'effective' and any(
                not _quote_matches(row.get(field), scene)
                for field in ('response_quote', 'change_quote')):
            raise StoryError('主审核心戏通过却缺少实际接招或变化证据')
        # A reviewer cannot evade the required core evidence by relabeling a
        # scene that the author explicitly planned as a core scene.
        if scene.get('scene_role') == 'core' and row['kind'] != 'core':
            raise StoryError('主审将已规划核心戏改标过渡，缺少核心变化核验')
    if not any(row['kind'] == 'core' for row in checks):
        raise StoryError('主审将全篇都标为过渡，缺少任何核心戏核验')
    premise = response.get('premise_test')
    _premise_review(premise, scenes, contract)
    allocation = _reading_allocation_check(response, plan, evidence_plan=evidence_plan)
    chapter_needs_replan = _chapter_checks(response, plan, evidence_plan=evidence_plan)
    if (chapter_needs_replan or check['status'] == 'needs_replan' or premise['status'] == 'needs_replan'
            or allocation['status'] == 'needs_replan'
            or any(row['status'] == 'needs_replan' for row in checks)):
        if response.get('decision') != 'revise' or not response.get('issues'):
            raise StoryError('主审发现结构缺口却未退回规划')
    if any(not _path_exists(issue.get('path'), plan) for issue in response.get('issues', [])):
        raise StoryError('主审大纲问题路径不属于真实方案，不能回修')
    return response


def validate_budgets(plan: dict, length: dict) -> list[int]:
    """Legacy positive numeric budgets remain valid and become relative weights.

    An integer is a planned visible-character target, not a strict per-scene
    minimum. The whole-story author contract remains the length authority.
    """
    if 'core_reading_experience' in plan and not _nonempty(plan['core_reading_experience']):
        raise StoryError('方案core_reading_experience必须明确核心阅读体验')
    budgets = []
    for index, scene in enumerate(plan.get('scenes') or []):
        value = scene.get('budget')
        if type(value) is not int or value <= 0:
            raise StoryError('场景budget必须是正整数正文目标，不能均分补齐或使用空值')
        role = scene.get('scene_role')
        if role is not None and role not in {'core', 'transition'}:
            raise StoryError('scene_role必须是core或transition')
        reading_role = scene.get('reading_role')
        if reading_role is not None and (not isinstance(reading_role, str) or reading_role not in READING_ROLES):
            raise StoryError('reading_role必须区分core_experience、support或mixed')
        if 'core_reading_experience' in plan and reading_role is None:
            raise StoryError('新方案须为每场标注reading_role，不能遗漏支撑任务与核心体验的区分')
        chain = scene.get('dramatic_chain')
        if chain is not None:
            error = 'dramatic_chain缺少行动、接招、调整、变化或删场作用'
            if not isinstance(chain, dict):
                raise StoryError(f'{error}：scenes[{index}].dramatic_chain必须为对象')
            missing = [f'scenes[{index}].dramatic_chain.{field}' for field in
                ('attempt', 'response', 'adjustment', 'change', 'next_effect', 'deletion_loss')
                if not _nonempty(chain.get(field))]
            if missing:
                raise StoryError(error + '：缺少非空字段 ' + '、'.join(missing))
        budgets.append(value)
    if not budgets:
        raise StoryError('场景预算没有可分配的场景')
    return budgets


def _weighted(total: int, weights: list[int]) -> list[int]:
    """Exactly apportion an integer total; retain a one-character floor."""
    if total < len(weights):
        raise StoryError('剩余篇幅不足以完成场景，请调整方案或前文；不会截断正文')
    distributable = total - len(weights)
    weight_sum = sum(weights)
    shares = [distributable * weight / weight_sum for weight in weights]
    result = [1 + math.floor(share) for share in shares]
    missing = total - sum(result)
    order = sorted(range(len(weights)), key=lambda index: shares[index] % 1, reverse=True)
    for index in order[:missing]:
        result[index] += 1
    return result


def allocate_budget(length: dict, scenes: list[dict], index: int, written: int) -> dict:
    """Follow authored scene proportions, reserving future dramatic room.

    Scene targets are soft. In particular, there is no evenly distributed floor
    that forces a short transition to expand. A finite author maximum reserves
    60% of future planned targets; unbounded stories keep useful ending targets
    even when the reference total has already been exceeded.
    """
    if type(written) is not int or written < 0 or not 0 <= index < len(scenes):
        raise ValueError('Invalid scene budget')
    weights = validate_budgets({'scenes': scenes}, length)
    normal = _weighted(length['target'], weights)
    remaining_weights = weights[index:]
    remaining = len(remaining_weights)
    ceiling = length.get('max')
    reserve = sum(max(1, math.floor(target * .6)) for target in normal[index + 1:])
    high = None if ceiling is None else ceiling - written - reserve
    # Only the ending bears the remaining author minimum. Earlier scenes may
    # finish as soon as their actual action and response are complete.
    low = max(1, length['min'] - written) if remaining == 1 else 1
    if remaining == 1 and low > max(math.ceil(normal[index] * 1.5), normal[index] + 500):
        raise StoryError('结构需退回规划层：前文篇幅缺口超过末场计划，不能靠异常扩写结尾凑足总量；请检查缺失的必要戏')
    if remaining > 1 and high is not None:
        high = min(high, math.ceil(normal[index] * 1.5))
    if high is not None and high < low:
        raise StoryError('剩余篇幅不足以完成场景，请调整方案或前文；不会截断正文')
    remaining_target = max(remaining, length['target'] - written)
    adaptive = _weighted(remaining_target, remaining_weights)[0]
    target = max(low, adaptive, math.ceil(normal[index] * .75))
    if high is not None:
        target = min(target, high)
    return {'min': low, 'max': high, 'target': target,
            'planned_target': normal[index], 'planned_budget': weights[index],
            'scene_role': scenes[index].get('scene_role', 'unspecified'),
            'allocation_protocol': PROTOCOL,
            'instruction': '目标允许浮动；过渡完成作用即可收束。总量不足应补必要的实际戏，不扩写手续、复述或核对过程。'}


def whole_allocations(length: dict, scenes: list[dict]) -> list[dict]:
    """Whole generation has no previously written prefix or ending deficit."""
    targets = _weighted(length['target'], validate_budgets({'scenes': scenes}, length))
    return [{'scene_id': scene['scene_id'], 'target': target,
             'scene_role': scene.get('scene_role', 'unspecified'),
             'instruction': '相对篇幅建议，过渡完成作用即可收束；不能为了目标扩写流程。'}
            for scene, target in zip(scenes, targets)]


def main_question(plan: dict, *, required: bool = False) -> str:
    """Use explicit authored questions only, never infer one from an ending."""
    question = next((value.strip() for key in (
        'main_question', 'story_question', 'opening_story_question', 'question')
        if isinstance(value := plan.get(key), str) and value.strip()), '')
    if not question and isinstance(plan.get('blueprint'), dict):
        value = plan['blueprint'].get('question')
        question = value.strip() if isinstance(value, str) else ''
    if required and not question:
        raise StoryError('方案缺少明确main_question，须重新规划；不能以候选口号或结局代填')
    return question


def continuation_context(plan: dict, current_index: int) -> dict:
    """Bounded whole-story orientation, separate from already-written facts."""
    def concise(scene):
        chain = scene.get('dramatic_chain') or {}
        return {'scene_id': scene['scene_id'], 'choice': str(scene.get('choice', ''))[:80],
                'change': str(chain.get('change') or scene.get('consequence', ''))[:80]}
    # These root fields occur in the planning interfaces or actual reviewed plans.
    # Prefer a nonblank question in this order; do not infer one from an ending
    # or a candidate's read_question, which describes a different input scope.
    question = main_question(plan, required=True)
    return {'main_question': question,
            'core_reading_experience': plan.get('core_reading_experience') or question,
            'current_reading_role': plan['scenes'][current_index].get('reading_role', 'unclassified'),
            'ending_resolution': str(plan.get('ending_resolution') or ''),
            'premise_test': str(plan.get('premise_test') or ''),
            'current_scene_id': plan['scenes'][current_index]['scene_id'],
            'story_route': [{'scene_id': scene['scene_id'],
                             'turn': str((scene.get('dramatic_chain') or {}).get('change')
                                         or scene.get('consequence', ''))[:70]}
                            for scene in plan['scenes']],
            'future_core_turns': [concise(scene) for scene in plan['scenes'][current_index + 1:]
                                  if scene.get('scene_role') != 'transition'][:3],
            'authority': '后续只是规划，不得当作已经发生的证据；当前场景必须为后续选择产生实际作用。'}
