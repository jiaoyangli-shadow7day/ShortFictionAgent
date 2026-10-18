"""Small, traceable inspiration cards; source pages never become prose prompts."""

from __future__ import annotations

from html.parser import HTMLParser
from html import unescape
import ipaddress
from pathlib import Path
import re
import socket
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
import xml.etree.ElementTree as ET

import requests

from .contracts import StoryError, digest
from .storage import now, read_json, write_json


SOURCE_TYPES = {"author_note", "public_fact", "market_signal", "licensed_work"}
RIGHTS = {"owned", "facts_only", "trend_only", "commercial_license", "unverified"}
MAX_PAGE_BYTES = 256_000


class _PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ""
        self.description = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta" and (values.get("name", "").lower() == "description" or
                              values.get("property", "").lower() == "og:description"):
            self.description = self.description or values.get("content", "")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


class _FanqieLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current_url = ""
        self.current_title = ""
        self.links = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a" and "title" in values.get("class", "").split():
            wrapped = values.get("href", "")
            target = parse_qs(urlparse(wrapped).query).get("targetUrl", [""])[0]
            self.current_url = target if target.startswith("https://fanqienovel.com/writer/zone/article/") else ""
            self.current_title = ""

    def handle_data(self, data):
        if self.current_url:
            self.current_title += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current_url:
            title = self.current_title.strip()
            if title:
                self.links.append((title, self.current_url))
            self.current_url = ""
            self.current_title = ""


def _check_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise StoryError("来源链接必须是公开 HTTPS 地址")
    host = parsed.hostname
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise StoryError(f"来源域名无法解析: {host}") from exc
    if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise StoryError("来源链接不得指向本机或私有网络")


def fetch_page_metadata(url: str) -> dict:
    """Fetch only page metadata, with a small byte cap and no redirect following."""
    _check_public_url(url)
    try:
        with requests.get(url, timeout=(5, 10), stream=True, allow_redirects=False,
                          headers={"User-Agent": "StoryStudio/2.0 inspiration-metadata"}) as response:
            if 300 <= response.status_code < 400:
                raise StoryError("来源页面发生重定向；请提供最终公开 HTTPS 链接")
            response.raise_for_status()
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise StoryError("来源链接不是 HTML 页面")
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=8192):
                size += len(chunk)
                if size > MAX_PAGE_BYTES:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
            parser = _PageMetadata()
            parser.feed(raw.decode(response.encoding or "utf-8", errors="replace"))
    except requests.RequestException as exc:
        raise StoryError(f"读取公开来源失败: {type(exc).__name__}") from exc
    return {"page_title": parser.title.strip()[:180],
            "page_description": parser.description.strip()[:400],
            "page_digest": digest(raw.decode("utf-8", errors="replace"))}


def load_seeds(root: Path) -> list[dict]:
    data = read_json(Path(root) / "inspiration" / "seeds.json", {"schema_version": 1, "seeds": []})
    if not isinstance(data, dict) or not isinstance(data.get("seeds"), list):
        raise StoryError("灵感池文件无效")
    return data["seeds"]


def seed_fingerprint(root: Path) -> str:
    return digest(load_seeds(root))


def add_seed(root: Path, *, source_type: str, summary: str, title: str = "",
             pattern: str = "", url: str = "", rights_status: str = "",
             license_name: str = "", attribution: str = "", fetch_metadata: bool = False) -> dict:
    if source_type not in SOURCE_TYPES:
        raise StoryError("未知灵感来源类型")
    summary = summary.strip()
    if not 10 <= len(summary) <= 1000:
        raise StoryError("灵感摘要应为10到1000字的事实或抽象观察")
    if len(pattern) > 300:
        raise StoryError("抽象模式过长")
    if source_type == "author_note" and url:
        raise StoryError("作者素材不需要公开链接")
    if source_type != "author_note" and not url:
        raise StoryError("外部灵感必须有可追溯来源链接")
    if url:
        _check_public_url(url)
    default_rights = {"author_note": "owned", "public_fact": "facts_only",
                      "market_signal": "trend_only", "licensed_work": "unverified"}
    rights_status = rights_status or default_rights[source_type]
    if rights_status not in RIGHTS:
        raise StoryError("未知权利状态")
    if source_type == "licensed_work" and rights_status == "commercial_license" and not license_name:
        raise StoryError("商业许可素材必须填写许可名称")
    if source_type == "licensed_work" and rights_status not in ("commercial_license", "unverified"):
        raise StoryError("作品素材必须记录许可或标记为未验证")
    metadata = fetch_page_metadata(url) if fetch_metadata else {}
    seeds = load_seeds(root)
    fingerprint = digest({"source_type": source_type, "url": url, "summary": summary})
    if any(seed.get("fingerprint") == fingerprint for seed in seeds):
        raise StoryError("该灵感已经导入")
    seed = {"id": str(uuid4()), "source_type": source_type,
            "title": title.strip() or metadata.get("page_title") or source_type,
            "factual_summary": summary, "abstract_pattern": pattern.strip(),
            "source_url": url, "captured_at": now(), "rights_status": rights_status,
            "license": license_name.strip(), "attribution": attribution.strip(),
            "page_metadata": metadata, "fingerprint": fingerprint}
    seeds.append(seed)
    write_json(Path(root) / "inspiration" / "seeds.json",
               {"schema_version": 1, "seeds": seeds})
    return seed


def prompt_cards(seeds: list[dict], *, limit: int = 8) -> list[dict]:
    """Only provenance-safe abstractions enter prompts, never downloaded page text."""
    allowed = [seed for seed in seeds if seed.get("source_type") != "licensed_work" or
               seed.get("rights_status") == "commercial_license"]
    return [{"id": s["id"], "type": s["source_type"], "summary": s["factual_summary"],
             "pattern": s.get("abstract_pattern", ""), "rights": s["rights_status"]}
            for s in allowed[:limit]]


def discover_sources(root: Path, *, query: str, domain: str, limit: int = 5) -> dict:
    """Search index titles/links only; a hit is not a verified factual seed."""
    query, domain = query.strip(), domain.strip().lower().removeprefix("www.")
    if not 2 <= len(query) <= 100 or not re.fullmatch(r"[a-z0-9.-]+", domain):
        raise StoryError("搜索词或域名无效")
    if not 1 <= limit <= 10:
        raise StoryError("单次搜索最多返回10条")
    _check_public_url(f"https://{domain}/")
    if domain == "fanqienovel.com":
        try:
            response = requests.get("https://fanqienovel.com/writer/zone/notice", timeout=(5, 15),
                                    headers={"User-Agent": "StoryStudio/2.0 inspiration-discovery"})
            response.raise_for_status()
            if len(response.content) > MAX_PAGE_BYTES:
                raise StoryError("作家资讯目录过大")
            parser = _FanqieLinks()
            parser.feed(response.text)
        except requests.RequestException as exc:
            raise StoryError(f"番茄公开目录读取失败: {type(exc).__name__}") from exc
        results = [{"title": title[:180], "url": url, "search_snippet": "",
                    "catalog": "番茄作家资讯"} for title, url in parser.links if query in title][:limit]
        discovery = {"query": query, "domain": domain, "discovered_at": now(),
                     "status": "official_catalog_titles_unverified_content", "results": results}
        write_json(Path(root) / "inspiration" / "discovery.json", discovery)
        return discovery
    try:
        response = requests.get("https://www.bing.com/search", timeout=(5, 15),
                                params={"q": f"site:{domain} {query}", "format": "rss"},
                                headers={"User-Agent": "StoryStudio/2.0 inspiration-discovery"})
        response.raise_for_status()
        if len(response.content) > MAX_PAGE_BYTES:
            raise StoryError("搜索响应过大")
        feed = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError) as exc:
        raise StoryError(f"公开来源搜索失败: {type(exc).__name__}") from exc
    results = []
    for item in feed.findall(".//item"):
        url = (item.findtext("link") or "").strip()
        host = (urlparse(url).hostname or "").lower()
        if urlparse(url).scheme != "https" or not (host == domain or host.endswith("." + domain)):
            continue
        title = unescape(re.sub(r"<[^>]+>", "", item.findtext("title") or "")).strip()
        snippet = unescape(re.sub(r"<[^>]+>", "", item.findtext("description") or "")).strip()
        if query.casefold() not in (title + " " + snippet).casefold():
            continue
        if title and url and url not in {result["url"] for result in results}:
            results.append({"title": title[:180], "url": url, "search_snippet": snippet[:400]})
        if len(results) >= limit:
            break
    discovery = {"query": query, "domain": domain, "discovered_at": now(),
                 "status": "unverified_search_results", "results": results}
    write_json(Path(root) / "inspiration" / "discovery.json", discovery)
    return discovery

