"""Source-linked text and image packages; no platform publication calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .contracts import StoryError, digest, visible_count
from .quality import current_review
from .storage import StoryProject, read_json, now, write_json, write_text


CHANNELS = {
    "generic_text": {"entry_type": "file", "status": "generic"},
    "fanqie_long": {"entry_type": "long_serial", "status": "unverified"},
    "fanqie_short": {"entry_type": "short_story", "status": "unverified"},
    "qidian": {"entry_type": "author_submission", "status": "unverified"},
    "douyin_carousel": {"entry_type": "image_carousel", "status": "unverified"},
}

RULE_SOURCES = {
    "fanqie_long": ["https://fanqienovel.com/writer/zone/article/7659317147297923134",
                     "https://fanqienovel.com/writer/zone/article/7642544663915675672"],
    "fanqie_short": ["https://fanqienovel.com/writer/zone/article/7409917439607062553"],
    "qidian": [],
    "douyin_carousel": ["https://www.douyin.com/video/7598010006927052041"],
}


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _font_path() -> str:
    candidates = [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise StoryError("没有找到可用于中文图文渲染的字体")


def _wrap(text: str, font, max_width: int) -> list[str]:
    lines = []
    for paragraph in text.splitlines() or [text]:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for character in paragraph:
            next_line = current + character
            if current and font.getlength(next_line) > max_width:
                lines.append(current)
                current = character
            else:
                current = next_line
        if current:
            lines.append(current)
    return lines


def render_cards(cards: list[dict], folder: Path, *, width: int = 1080, height: int = 1440) -> list[Path]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise StoryError("图文导出需要 Pillow，请安装项目依赖") from exc
    if (width, height) not in ((1080, 1440), (1080, 1920)):
        raise StoryError("仅支持已验证排版的 1080x1440 或 1080x1920")
    font_path = _font_path()
    folder.mkdir(parents=True, exist_ok=True)
    outputs = []
    for index, card in enumerate(cards, 1):
        title = str(card.get("title") or "").strip()
        body = str(card.get("text") or "").strip()
        if not body:
            raise StoryError(f"第{index}张卡片正文为空")
        image = Image.new("RGB", (width, height), (249, 247, 241))
        draw = ImageDraw.Draw(image)
        draw.rectangle((58, 58, width - 58, height - 58), outline=(207, 201, 186), width=2)
        available_width = width - 220
        title_font = ImageFont.truetype(font_path, 58)
        title_lines = _wrap(title, title_font, available_width) if title else []
        title_height = len(title_lines) * 87
        if len(title_lines) > 3:
            raise StoryError(f"第{index}张卡片标题过长")
        y = 165
        for line in title_lines:
            draw.text((110, y), line, font=title_font, fill=(35, 35, 33))
            y += 87
        y += 60 if title else 0
        bottom = height - 185
        chosen = None
        for size in (54, 50, 46, 42):
            font = ImageFont.truetype(font_path, size)
            lines = _wrap(body, font, available_width)
            line_height = round(size * 1.75)
            if y + len(lines) * line_height <= bottom:
                chosen = (font, lines, line_height)
                break
        if chosen is None:
            raise StoryError(f"第{index}张卡片文字溢出；请缩短卡片文案")
        font, lines, line_height = chosen
        for line in lines:
            draw.text((110, y), line, font=font, fill=(42, 42, 40))
            y += line_height
        small = ImageFont.truetype(font_path, 30)
        counter = f"{index:02d} / {len(cards):02d}"
        draw.text((width - 110 - small.getlength(counter), height - 135), counter,
                  font=small, fill=(110, 107, 99))
        output = folder / f"{index:03d}.png"
        image.save(output)
        outputs.append(output)
    sheet = Image.new("RGB", (300 * min(4, len(outputs)), 410 * ((len(outputs) + 3) // 4)), "white")
    for index, output in enumerate(outputs):
        with Image.open(output) as image:
            preview = image.copy()
            preview.thumbnail((290, 390))
            sheet.paste(preview, ((index % 4) * 300, (index // 4) * 410))
    sheet.save(folder / "contact-sheet.png")
    return outputs


class StoryExporter:
    def __init__(self, project: StoryProject, call_json=None):
        self.project = project
        self.call_json = call_json

    def export(self, channel: str, *, draft: bool = False, card_size: tuple[int, int] = (1080, 1440),
               category: str | None = None, titles: list[str] | None = None,
               synopsis: str | None = None, platform_confirmation: dict | None = None,
               publication_as_of: str | None = None) -> Path:
        if channel not in CHANNELS:
            raise StoryError(f"未知导出渠道: {channel}")
        supplied_metadata = titles is not None or synopsis is not None
        if supplied_metadata and (
                not isinstance(titles, list) or not 1 <= len(titles) <= 3
                or any(not isinstance(t, str) or not 1 <= len(t.strip()) <= 80 for t in titles)
                or not isinstance(synopsis, str) or len(synopsis) > 2000):
            raise StoryError("指定发布资料时须同时提供有效标题列表和简介")
        record, body = self.project.active_draft()
        contract = self.project.contract()
        if contract.get("market_contract") and channel != "generic_text":
            from .market import MARKET_PROFILES
            expected = MARKET_PROFILES[contract["market_contract"]["profile_id"]]["required_channel"]
            if channel != expected:
                raise StoryError("导出渠道与已审查市场合同不符；请建立该渠道合同并重新审查")
        if channel == "fanqie_long" and contract["format"] != "long_serial":
            raise StoryError("番茄长篇包只适用于 long_serial；请选择番茄短故事或通用文本")
        report = None
        if not draft:
            if record["state"] != "passed":
                raise StoryError("正式发布包需要当前正文通过审查；可用 --draft 输出标记草稿的包")
            report = current_review(self.project)
            if channel in ("fanqie_short", "fanqie_long") and any(c in body for c in '「」『』'):
                raise StoryError('番茄正式发布包须使用中文双引号“”；请保存排版修订并重新审读，不能静默修改已审正文')
        publication_result = None
        if channel == "fanqie_short":
            from .publication import publication_check
            publication_result = publication_check(
                body, contract, category=category, channel=channel,
                platform_confirmation=platform_confirmation, as_of=publication_as_of)
            if not draft and publication_result["activity"] and publication_result["blockers"]:
                raise StoryError("所选活动投稿目标不符：" + "；".join(
                    row["reason"] for row in publication_result["checks"] if row["status"] == "mismatch"))
            if not draft and "official_category" in publication_result["blockers"]:
                raise StoryError("导出主分类与已审查投稿合同不符；请修改合同并重新审查")
        title = contract.get("title") or contract["author_input"]["premise"][:26] or "未命名故事"
        synopsis = synopsis if supplied_metadata else body[:180].strip().replace("\n", "")
        titles = list(titles) if supplied_metadata else [title]
        chief_mode = bool(report and report.get('review_mode') == 'chief_editor')
        if self.call_json and not draft and not supplied_metadata and not chief_mode:
            from .submission_guide import submission_guide_guidance
            metadata = self.call_json(
                "channel_metadata",
                "为此原创故事写3个真实、准确的中文标题和一段不剧透的简介。"
                "三个标题分别尝试具体关系/行动事件、反常选择或身份矛盾、短意象；"
                "按本篇承诺取舍，不为点击添加出轨等误读。标题之间的信息组织须有差异。"
                "只输出JSON对象titles数组和synopsis字符串；不编造原文没有的事实。\n"
                + submission_guide_guidance(contract, "lead") + "\n"
                + json.dumps({"channel": channel, "contract": contract, "body": body}, ensure_ascii=False),
            )
            if not isinstance(metadata.get("titles"), list) or not metadata["titles"]:
                raise StoryError("标题生成未返回有效列表")
            titles = [str(x) for x in metadata["titles"][:3]]
            synopsis = str(metadata.get("synopsis") or "")
        from .submission_guide import (submission_guide_context, submission_guide_format_errors,
                                       submission_guide_review_errors)
        guide_context = submission_guide_context(contract, "publication")
        guide_errors = submission_guide_format_errors(body, contract, title=titles[0])
        review_errors = submission_guide_review_errors(report or {}, body, contract)
        if not draft and guide_errors + review_errors:
            raise StoryError("；".join(guide_errors + review_errors))
        guide_check = {"context": guide_context, "content_hash": digest(body),
                       "review_id": report.get("review_id") if report else None,
                       "application_binding": "current" if not review_errors else "missing_or_stale",
                       "format_errors": guide_errors, "application_errors": review_errors,
                       "meaning": "核验指南输入版本和所选投稿格式；语义质量由绑定当前正文的主审报告判定。"}
        preparation = None
        if not draft and channel in ("fanqie_short", "douyin_carousel"):
            from .distribution import PublicationPreparationAgent, chief_preparation
            prepare = (lambda **kwargs: chief_preparation(self.project, self.call_json, **kwargs)) if chief_mode else PublicationPreparationAgent(self.project, self.call_json).review
            declared_category = (contract.get("publication_contract") or {}).get("official_category") or ''
            actual_category = (category if category is not None else declared_category) if chief_mode else (
                category or declared_category or contract["genre"]["primary"])
            preparation = prepare(
                channel=channel, titles=titles, synopsis=synopsis,
                category=actual_category)
            if preparation["decision"] != "editorial_ready":
                raise StoryError("发布准备审读需要修订；请查看 publication_preparation 记录")
        package_id = str(uuid4())
        package = self.project.root / "exports" / package_id
        staging = self.project.root / "exports" / f".{package_id}.building"
        staging.mkdir(parents=True, exist_ok=False)
        try:
            self._build_package(staging, channel, record, body, contract, report,
                                titles, synopsis, draft, card_size,
                                category=preparation['inputs']['category'] if preparation else category,
                                publication_result=publication_result, guide_check=guide_check)
            from .research_guidance import research_context
            write_json(staging / "research_context.json", research_context(contract, task="channel_metadata"))
            manifest = read_json(staging / "manifest.json")
            manifest["files_sha256"]["research_context.json"] = digest((staging / "research_context.json").read_text(encoding="utf-8"))
            write_json(staging / "manifest.json", manifest)
            if preparation:
                write_json(staging / "publication_preparation.json", preparation)
                manifest = read_json(staging / "manifest.json")
                manifest["files_sha256"]["publication_preparation.json"] = digest(
                    (staging / "publication_preparation.json").read_text(encoding="utf-8"))
                write_json(staging / "manifest.json", manifest)
            staging.rename(package)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self.project.record_run({"event": "export", "channel": channel, "package_id": package.name})
        return package

    def _build_package(self, package: Path, channel: str, record: dict, body: str,
                       contract: dict, report: dict | None, titles: list[str],
                       synopsis: str, draft: bool, card_size: tuple[int, int],
                       category: str | None = None, publication_result: dict | None = None,
                       guide_check: dict | None = None) -> None:
        write_text(package / "manuscript.txt", body)
        write_text(package / "manuscript.md", body)
        if guide_check and guide_check["context"]["enabled"]:
            write_json(package / "submission_guide_check.json", guide_check)
        from . import lead
        if lead.enabled(contract):
            try:
                lead_text, story_text = lead.split(body)
            except StoryError:
                if not draft:
                    raise
            else:
                # Convenience copies; the complete submission manuscript already includes both.
                write_text(package / 'lead.txt', lead_text + '\n')
                write_json(package / 'opening_structure.json', {
                    'mode': 'lead_then_chapter', 'protocol': lead.PROTOCOL,
                    'content_hash': digest(body), 'lead_hash': digest(lead_text),
                    'lead_visible_chars': visible_count(lead_text),
                    'meaning': 'manuscript.txt已包含导语、第一章分界和完整正文；lead.txt仅供单独审阅。'})
        if contract["format"] == "short_serial":
            episode_parts = re.split(r"(?m)^=== 第(\d+)集 ===\s*$", body)
            if len(episode_parts) < 7 or episode_parts[0].strip():
                raise StoryError("短连载正文没有有效的分集标记")
            for index in range(1, len(episode_parts), 2):
                number, episode_body = int(episode_parts[index]), episode_parts[index + 1].strip()
                if not episode_body:
                    raise StoryError(f"第{number}集为空")
                write_text(package / "episodes" / f"{number:03d}.txt", episode_body + "\n")
        write_text(package / "titles.md", "\n".join(f"- {x}" for x in titles) + "\n")
        write_text(package / "synopsis.md", synopsis + "\n")
        tags = list((contract.get("research_preferences") or {}).get("reader_tags") or [])
        submission_category = category or contract["genre"]["primary"]
        if publication_result is not None:
            # Narrative genre remains in metadata; only recorded backend labels go here.
            submission_category = publication_result["official_category"]
        if submission_category == "custom":
            submission_category = None
        submission = {"title": titles[0], "synopsis": synopsis,
                      "recommended_category": submission_category,
                      "recommended_tags": tags,
                      "tag_basis": "author_research_preferences",
                      "tag_scope": "推荐的读者标签；不等同已核验的作者后台分类",
                      "source_revision": record["revision"],
                      "source_content_hash": record["content_hash"], "created_at": now(),
                      "delivery_status": "review_copy" if draft else "editorially_ready",
                      "publication_status": publication_result["status"] if publication_result else "not_assessed",
                      "official_category_status": (publication_result["states"]["category_status"]
                                                   if publication_result else "unverified"),
                      "publication_contract": contract.get("publication_contract", {}),
                      "submission_guide": guide_check["context"] if guide_check else None,
                      "platform_eligibility": "unverified",
                      "review_id": report["review_id"] if report else None,
                      "delivery_presentation": {
                          "mode": "individual_file_links", "archive": False,
                          "reply_fields": ["title", "character_count", "manuscript_link",
                                           "submission_info_link", "category", "tags", "opening_excerpt"],
                          "instruction": ("审阅草稿，仅在用户要求预览时交付；不得作为投稿成品。"
                                          if draft else
                                          "直接提供正文与投稿说明的文件链接，在回复中列出标题、字数、分类、标签和简短开篇。"),
                      }}
        write_json(package / "submission_info.json", submission)
        write_text(package / "submission_info.txt", "\n".join([
            "交付状态：" + ("审阅草稿，未完成正式交付检查，不可作为投稿成品。" if draft else "已通过当前正文及本地发布准备审读；平台状态另行核验。"),
            "标题：" + titles[0], "简介：" + synopsis,
            "推荐分类：" + (submission_category or "待选择"),
            "推荐标签：" + ("、".join(tags) or "待选择"),
            "标签依据：作者简报；按投稿入口可选项填写。",
            "投稿目标核对：" + (publication_result["status"] if publication_result else "未配置"),
            "平台计字：" + (str(publication_result["platform_confirmation"]["word_count"])
                               if publication_result and publication_result["platform_confirmation"] else "待作者后台确认"),
            f"正文版本：{record['revision']}",
        ]) + "\n")
        write_json(package / "metadata.json", {"title": titles[0], "titles": titles,
                                                "synopsis": synopsis, "genre": contract["genre"],
                                                "format": contract["format"],
                                                "publication_category": submission_category,
                                                "publication_tags": tags,
                                                "publication_contract": contract.get("publication_contract", {}),
                                                "publication_status": publication_result["status"] if publication_result else "not_assessed",
                                                "market_contract": contract.get("market_contract", {}),
                                                "market_assessment": "editorially_reviewed" if contract.get("market_contract") and report else "not_assessed",
                                                "commercial_validation": "unverified"})
        from .distribution import audit_workflow
        write_json(package / "workflow_audit.json", audit_workflow(self.project))
        write_json(package / "provenance.json", {"source_artifact_id": record["artifact_id"],
                                                  "source_revision": record["revision"],
                                                  "source_content_hash": record["content_hash"],
                                                  "ai_assisted": True, "human_edit_status": "unknown"})
        lines = [f"# {channel} 导出核对", "", f"- 正文状态: {record['state']}",
                 f"- 渠道资格: {CHANNELS[channel]['status']}",
                 "- 发布前请在对应作者后台核对当前入口、字数和AI内容标识要求。",
                 "- 本包尚未在任何平台发布。"]
        if publication_result is not None:
            write_json(package / "publication_check.json", publication_result)
            lines += [f"- 投稿目标核对: {publication_result['status']}",
                      f"- 本地可见字符: {publication_result['local_count']['value']}；平台计字单独确认。",
                      f"- 正式主分类: {publication_result['official_category'] or '待确认'}",
                      "- " + publication_result["meaning"]]
            lines.extend("- " + row["reason"] for row in publication_result["checks"] if row["status"] != "ready")
        for source in RULE_SOURCES.get(channel, []):
            lines.append(f"- 官方公开资料: {source}")
        write_text(package / "channel_check.md", "\n".join(lines) + "\n")
        if channel == "douyin_carousel":
            if not self.call_json:
                raise StoryError("图文改编需要模型接口")
            response = self.call_json(
                "carousel_adapt",
                "将完整故事改编为6到12张可阅读的图文卡片。输出JSON对象cards数组；"
                "每张含title(可空),text,source_quote。source_quote必须是正文连续原文。"
                "首图提出具体处境，中间每张有新信息，末页兑现本集问题；"
                "事实不可超出原文。每张建议50到140个中文字符，太长会被排版拒绝。\n"
                + json.dumps({"title": titles[0], "body": body}, ensure_ascii=False),
            )
            cards = response.get("cards")
            if not isinstance(cards, list) or not 6 <= len(cards) <= 12:
                raise StoryError("图文改编需返回6到12张卡片")
            sources = []
            for index, card in enumerate(cards, 1):
                quote = card.get("source_quote")
                if not isinstance(quote, str) or quote not in body:
                    raise StoryError(f"第{index}张卡片缺少有效原文出处")
                sources.append({"card": index, "source_span": [body.index(quote), body.index(quote) + len(quote)],
                                "source_quote": quote})
            adaptation_review = self.call_json(
                "carousel_review",
                "逐页核对图文卡片与原故事：事实、因果、标题承诺、本集结尾回报。"
                "输出JSON对象passed布尔值、issues数组；有虚构关键事实或结尾未兑现时passed=false。\n"
                + json.dumps({"body": body, "cards": cards}, ensure_ascii=False),
            )
            if adaptation_review.get("passed") is not True or adaptation_review.get("issues"):
                raise StoryError("图文语义复审未通过，请检查卡片改编后重试")
            render_cards(cards, package / "cards", width=card_size[0], height=card_size[1])
            write_json(package / "cards" / "source.json", {"cards": cards, "sources": sources})
            write_json(package / "cards" / "review.json", adaptation_review)
        files = {str(p.relative_to(package)): _file_hash(p) for p in package.rglob("*") if p.is_file()}
        manifest = {"package_id": package.name.lstrip(".").removesuffix(".building"), "channel": channel,
                    "delivery_status": "review_copy" if draft else "editorially_ready",
                    "entry_type": CHANNELS[channel]["entry_type"],
                    "channel_status": CHANNELS[channel]["status"],
                    "editorial_state": record["state"], "review_id": report["review_id"] if report else None,
                    "source_artifact_id": record["artifact_id"], "source_revision": record["revision"],
                    "source_content_hash": record["content_hash"], "contract_hash": digest(contract),
                    "exported_at": now(), "published": False, "files_sha256": files,
                    "publication_status": publication_result["status"] if publication_result else "not_assessed",
                    "activity_window_status": publication_result["activity_window_status"] if publication_result else None,
                    "rule_sources": publication_result["sources"] if publication_result else RULE_SOURCES.get(channel, []),
                    "rules_checked_at": publication_result["rules_checked_at"] if publication_result else None}
        if guide_check and guide_check["context"]["enabled"]:
            manifest["submission_guide"] = {k: guide_check["context"][k]
                                             for k in ("profile_id", "version", "guide_hash", "scope")}
        write_json(package / "manifest.json", manifest)
