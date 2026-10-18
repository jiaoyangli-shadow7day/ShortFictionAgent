"""文件存储工具。"""

import json
import yaml
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


def ensure_dir(path: Path):
    """确保目录存在。"""
    path.mkdir(parents=True, exist_ok=True)

def backup_file(path: Path):
    """备份文件（加时间戳）。"""
    if not path.exists():
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".backup_{timestamp}{path.suffix}")
    shutil.copy2(path, backup_path)

def safe_write_json(path: Path, data, backup: bool = True):
    """安全写入 JSON 文件（先备份再写入）。"""
    ensure_dir(path.parent)
    if backup and path.exists():
        backup_file(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def safe_read_json(path: Path) -> dict:
    """安全读取 JSON 文件。"""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def safe_write_yaml(path: Path, data, backup: bool = True):
    """安全写入 YAML 文件。"""
    ensure_dir(path.parent)
    if backup and path.exists():
        backup_file(path)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

def safe_read_yaml(path: Path) -> dict:
    """安全读取 YAML 文件。"""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return yaml.safe_load(content) or {}
    except (yaml.YAMLError, FileNotFoundError):
        return {}

def safe_write_markdown(path: Path, content: str, backup: bool = True):
    """安全写入 Markdown 文件。"""
    ensure_dir(path.parent)
    if backup and path.exists():
        backup_file(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def safe_read_markdown(path: Path) -> str:
    """安全读取 Markdown 文件。"""
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

def safe_read_text(path: Path) -> str:
    """读取纯文本文件。"""
    return safe_read_markdown(path)

