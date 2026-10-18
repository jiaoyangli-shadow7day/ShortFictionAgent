"""Atomic, versioned local project storage."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

import yaml

from .contracts import StoryBrief, StoryError, clean_body, digest


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


class StoryProject:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    @property
    def brief_path(self) -> Path:
        return self.root / "brief.yaml"

    def create(self, brief: StoryBrief) -> None:
        if self.brief_path.exists():
            raise StoryError(f"项目已存在: {self.brief_path}")
        write_text(self.brief_path, yaml.safe_dump(brief.to_dict(), allow_unicode=True, sort_keys=False))
        write_json(self.root / "effective_contract.json", brief.effective_contract())
        write_json(self.root / "project.json", {"schema_version": 2, "project_id": brief.project_id,
                                                   "active_artifact_id": None, "created_at": now()})

    def brief(self) -> StoryBrief:
        if not self.brief_path.exists():
            raise StoryError(f"项目没有 brief.yaml: {self.root}")
        return StoryBrief.from_dict(yaml.safe_load(self.brief_path.read_text(encoding="utf-8")))

    def contract(self) -> dict:
        contract = self.brief().effective_contract()
        write_json(self.root / "effective_contract.json", contract)
        return contract

    def record_run(self, event: dict) -> None:
        path = self.root / "runs" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"at": now(), **event}, ensure_ascii=False) + "\n")

    def reject_draft(self, reason: str, *, source: str = 'author') -> dict:
        """Keep an explicit editorial rejection authoritative for this exact prose."""
        if not isinstance(reason, str) or not reason.strip():
            raise StoryError('退稿须有具体原因')
        record, body = self.active_draft()
        rejection = {'created_at': now(), 'content_hash': digest(body),
                     'artifact_id': record['artifact_id'], 'revision': record['revision'],
                     'decision': 'reject', 'source': source, 'reason': reason.strip()}
        path = self.root / 'independent_veto.json'
        if path.exists():
            write_json(self.root / 'independent_veto_history' / f'{uuid4()}.json', read_json(path))
        write_json(path, rejection)
        write_json(self.root / 'editorial_feedback.json', rejection)
        self.update_state(record, 'revise_required')
        self.record_run({'event': 'editorial_rejection', **rejection})
        return rejection

    def save_draft(self, body: str, *, artifact_id: str | None = None, parent_revision: int | None = None) -> dict:
        body = clean_body(body)
        artifact_id = artifact_id or str(uuid4())
        base = self.root / "drafts" / artifact_id
        existing = sorted(int(p.name) for p in base.iterdir() if p.is_dir() and p.name.isdigit()) if base.exists() else []
        if existing and parent_revision != existing[-1]:
            raise StoryError("修订的父版本不等于当前版本")
        revision = (existing[-1] + 1) if existing else 1
        if not existing and parent_revision is not None:
            raise StoryError("新稿不应指定父版本")
        folder = base / str(revision)
        write_text(folder / "body.md", body)
        record = {"artifact_id": artifact_id, "revision": revision,
                  "content_hash": digest(body), "contract_hash": digest(self.contract()),
                  "state": "draft", "created_at": now()}
        write_json(folder / "artifact.json", record)
        project = read_json(self.root / "project.json", {})
        project["active_artifact_id"] = artifact_id
        project["active_revision"] = revision
        write_json(self.root / "project.json", project)
        self._mark_dependent_artifacts_stale(record)
        self.record_run({"event": "draft_saved", "artifact_id": artifact_id, "revision": revision})
        return record

    def _mark_dependent_artifacts_stale(self, current: dict) -> None:
        """Preserve provenance while making older releases visibly unusable."""
        for manifest_path in (self.root / "exports").glob("*/manifest.json"):
            manifest = read_json(manifest_path)
            if (manifest.get("source_artifact_id"), manifest.get("source_revision")) != (
                    current["artifact_id"], current["revision"]):
                write_json(manifest_path.parent / "status.json", {
                    "state": "stale", "current_artifact_id": current["artifact_id"],
                    "current_revision": current["revision"], "marked_at": now()})
        for commit_path in (self.root / "memory" / "commits").glob("*.json"):
            if commit_path.name.endswith(".status.json"):
                continue
            commit = read_json(commit_path)
            if (commit.get("artifact_id"), commit.get("revision")) != (
                    current["artifact_id"], current["revision"]):
                write_json(commit_path.with_suffix(".status.json"), {
                    "state": "stale", "current_artifact_id": current["artifact_id"],
                    "current_revision": current["revision"], "marked_at": now()})

    def active_draft(self) -> tuple[dict, str]:
        project = read_json(self.root / "project.json", {})
        artifact_id, revision = project.get("active_artifact_id"), project.get("active_revision")
        if not artifact_id or not revision:
            raise StoryError("项目尚无正文草稿")
        folder = self.root / "drafts" / artifact_id / str(revision)
        record = read_json(folder / "artifact.json")
        body = (folder / "body.md").read_text(encoding="utf-8")
        if digest(body) != record["content_hash"]:
            raise StoryError("正文在保存后发生变化；旧审查与导出均已失效")
        return record, body

    def update_state(self, record: dict, state: str) -> dict:
        current, _ = self.active_draft()
        if current["artifact_id"] != record["artifact_id"] or current["revision"] != record["revision"]:
            raise StoryError("只能更新当前稿件的状态")
        current["state"] = state
        folder = self.root / "drafts" / current["artifact_id"] / str(current["revision"])
        write_json(folder / "artifact.json", current)
        self.record_run({"event": "state_changed", "state": state,
                         "artifact_id": current["artifact_id"], "revision": current["revision"]})
        return current
