"""配置管理与加载。"""

import os
import yaml
from pathlib import Path
from typing import Optional
from .models import NovelConfig, ModelConfig

def load_yaml(path: Path) -> dict:
    """加载 YAML 文件，返回字典。"""
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def save_yaml(path: Path, data: dict):
    """保存字典为 YAML 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

def load_json(path: Path) -> dict:
    """加载 JSON 文件。"""
    import json
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: Path, data, pretty: bool = True):
    """保存 JSON 文件。"""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            json.dump(data, f, ensure_ascii=False)

def load_novel_config(project_path: Path) -> NovelConfig:
    """加载小说配置。"""
    path = project_path / "config" / "novel.yaml"
    data = load_yaml(path)
    return NovelConfig.from_dict(data)

def load_model_config(project_path: Path) -> ModelConfig:
    """加载模型配置（兼容单模型和双模型格式）。

    API Key 优先级: 环境变量 > 配置文件。
    支持 LLM_API_KEY 或 DEEPSEEK_API_KEY。
    """
    path = project_path / "config" / "model.yaml"
    data = load_yaml(path)
    env_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY", "")

    # Support new multi-model format: {primary: {...}, fallback: {...}}
    if "primary" in data:
        primary = dict(data["primary"])
        primary["api_key"] = primary.get("api_key", "") or env_key
        return ModelConfig.from_dict(primary)

    # Legacy single-model format
    api_key = data.get("api_key", "") or env_key
    data["api_key"] = api_key
    return ModelConfig.from_dict(data)


def load_multi_model_config(project_path):
    """加载双模型配置（primary + fallback）。

    API Key 优先级: 环境变量 > 配置文件。
    """
    from .models import MultiModelConfig
    project_path = Path(project_path)
    path = project_path / "config" / "model.yaml"
    data = load_yaml(path)
    env_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY", "")

    if "primary" in data:
        mc = MultiModelConfig.from_dict(data)
        for cfg in [mc.primary, mc.fallback]:
            if cfg and not cfg.api_key:
                cfg.api_key = env_key
        return mc

    # Legacy single-model
    mc = ModelConfig.from_dict(data)
    if not mc.api_key:
        mc.api_key = env_key
    return MultiModelConfig(primary=mc, fallback=None)

def load_routing_config(project_path: Path) -> dict:
    """加载模型路由配置（config/model.yaml 的可选 routing 段）。

    没有配置时返回空 dict，路由器会使用内置默认表。
    """
    path = Path(project_path) / "config" / "model.yaml"
    data = load_yaml(path)
    routing = data.get("routing") if isinstance(data, dict) else None
    return routing if isinstance(routing, dict) else {}


def load_style_config(project_path: Path) -> dict:
    """加载风格配置。"""
    path = project_path / "config" / "style.yaml"
    return load_yaml(path)

def load_character(project_path: Path, role: str) -> dict:
    """加载角色设定。"""
    mapping = {
        "protagonist_male": "protagonist_male.yaml",
        "protagonist_female": "protagonist_female.yaml",
        "supporting": "supporting_characters.yaml",
    }
    path = project_path / "characters" / mapping.get(role, f"{role}.yaml")
    return load_yaml(path)

def load_campus_setting(project_path: Path) -> dict:
    """加载学校设定。"""
    path = project_path / "campus" / "school_setting.yaml"
    return load_yaml(path)

def load_markdown(path: Path) -> str:
    """加载 Markdown 文件。"""
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def save_markdown(path: Path, content: str):
    """保存 Markdown 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def load_text(path: Path) -> str:
    """加载文本文件。"""
    return load_markdown(path)

def save_text(path: Path, content: str):
    """保存文本文件。"""
    save_markdown(path, content)

