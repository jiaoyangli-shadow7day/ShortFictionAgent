"""SettingAgent - 负责创建小说基础设定。"""

import yaml
from pathlib import Path
from ..config import save_yaml, save_markdown
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class SettingAgent:
    """创建和维护小说基础设定。"""

    def __init__(self, paths: ProjectPaths, llm: LLMClient, pack=None):
        from ..genres import resolve_project_pack
        self.paths = paths
        self.llm = llm
        self.pack = pack or resolve_project_pack(paths)
        self.runtime = AgentRuntime(paths, llm)

    def create_default_settings(self, title: str = "未命名", stage: str = "高中", target_words: int = 200000):
        """生成默认设定文件（不调用 LLM）。题材默认值来自 GenrePack.scaffolding。"""
        self.paths.ensure_dirs()
        scaffold = self.pack.scaffolding or {}
        novel_defaults = scaffold.get("novel", {})
        style_defaults = scaffold.get("style", {})

        # 默认 novel.yaml（题材相关字段来自题材包）
        novel_config = {
            "title": title,
            "genre": novel_defaults.get("genre", self.pack.display_name),
            "sub_genre": "",
            "stage": stage,
            "target_words": target_words,
            "tone": novel_defaults.get("tone", "克制、真实"),
            "pace": novel_defaults.get("pace", "中等"),
            "perspective": novel_defaults.get("perspective", "第三人称有限视角"),
            "protagonist_male_name": "",
            "protagonist_female_name": "",
            "setting_description": "",
            "core_theme": "",
        }
        save_yaml(self.paths.novel_config, novel_config)

        # 默认 model.yaml
        model_config = {
            "provider": "mock",
            "base_url": "",
            "api_key": "",
            "model": "",
            "temperature": 0.8,
            "max_tokens": 6000,
        }
        save_yaml(self.paths.model_config, model_config)

        # 默认 style.yaml（题材相关的禁用/必备元素来自题材包）
        style_config = {
            "narrative_style": novel_config["perspective"],
            "dialogue_style": "自然口语化，符合人物身份的真实对话",
            "description_style": "重细节轻抒情，用场景说话",
            "pace_style": novel_config["pace"],
            "chapter_length": "2000-3500字",
            "prohibited_expressions": style_defaults.get("prohibited_expressions", [
                "命运的齿轮开始转动", "他不知道的是", "一眼万年", "命中注定",
            ]),
            "required_elements": style_defaults.get("required_elements", [
                "具体生活细节", "动作而非心理总结", "对话而非旁白解释",
                "场景感官细节（声音、光线、气味）",
            ]),
        }
        save_yaml(self.paths.style_config, style_config)

        return {
            "novel_config": novel_config,
            "model_config": model_config,
            "style_config": style_config,
        }

    def generate_with_llm(self, title: str, stage: str, target_words: int):
        """使用 LLM 生成完整小说设定。"""
        # 读取设定 Prompt 模板与题材写作规则(均经题材包解析, 题材专属优先回退共享)
        setting_prompt = self.pack.read_prompt("setting_prompt.md")
        rules = self.pack.rules_text()

        full_prompt = f"""{setting_prompt}

## {self.pack.display_name}写作规则
{rules}

## 用户输入
- 书名：{title}
- 阶段：{stage}
- 目标字数：{target_words}

请生成完整的小说设定，按照上述格式输出 YAML。"""

        policy = AgentCallPolicy(
            agent_name="SettingAgent",
            task_type="setting",
            target_prompt_tokens=16000,
            min_response_chars=800,
            allow_fallback=True,
        )
        response = self.runtime.call(full_prompt, policy)
        return self._parse_setting_response(response)

    def _parse_setting_response(self, response: str) -> dict:
        """解析 LLM 返回的设定，提取 YAML 部分。"""
        # 提取 YAML 块
        lines = response.split("\n")
        yaml_lines = []
        in_yaml = False
        for line in lines:
            if line.strip().startswith("```"):
                if in_yaml:
                    break
                in_yaml = True
                continue
            if in_yaml:
                yaml_lines.append(line)

        if yaml_lines:
            try:
                return yaml.safe_load("\n".join(yaml_lines))
            except yaml.YAMLError:
                pass
        return {}

    def apply_llm_settings(self, settings: dict):
        """将 LLM 生成的设定写入文件（题材无关: 角色按题材角色槽位落盘）。"""
        self.paths.ensure_dirs()

        if "novel" in settings:
            save_yaml(self.paths.novel_config, settings["novel"])
        if "initial_relationship" in settings:
            save_yaml(self.paths.relationship_state, settings["initial_relationship"])
        # 世界/背景设定(校园=学校, 都市=公司/城市, 玄幻=门派…)
        for wkey in ("world_setting", "school_setting"):
            if wkey in settings:
                save_yaml(self.paths.world_setting_file, settings[wkey])
                break
        # 角色: 题材包定义的角色槽位 + 任何 protagonist*/character* 键
        roles = set(self.pack.character_roles)
        for key, val in settings.items():
            if key in roles or key.startswith("protagonist") or key.startswith("character_"):
                save_yaml(self.paths.character_file(key), val)

    def get_status(self) -> dict:
        """获取设定模块状态。"""
        return {
            "novel_config": self.paths.novel_config.exists(),
            "model_config": self.paths.model_config.exists(),
            "style_config": self.paths.style_config.exists(),
            "protagonist_male": self.paths.protagonist_male.exists(),
            "protagonist_female": self.paths.protagonist_female.exists(),
            "supporting_characters": self.paths.supporting_characters.exists(),
            "relationship_state": self.paths.relationship_state.exists(),
            "school_setting": self.paths.school_setting.exists(),
        }

