from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiohttp
from bilibili_api import video as bili_video
from bilibili_api.utils.network import Credential

from .canonical import SourceUnit, units_from_bilibili_body


@dataclass(frozen=True)
class BiliResult:
    units: list[SourceUnit]
    metadata: dict


def extract_bvid(value: str) -> str:
    match = re.search(r"BV[0-9A-Za-z]{10}", value)
    if not match:
        raise ValueError(f"无法从 {value!r} 提取 BV 号")
    return match.group(0)


def _page_number(value: str) -> int:
    try:
        return max(1, int(parse_qs(urlparse(value).query).get("p", ["1"])[0]))
    except (TypeError, ValueError):
        return 1


def _credential(path: Path) -> Credential:
    if not path.is_file():
        raise RuntimeError(f"未找到 B 站凭证：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("sessdata"):
        raise RuntimeError(f"B 站凭证缺少 sessdata：{path}")
    return Credential(
        sessdata=data.get("sessdata", ""),
        bili_jct=data.get("bili_jct", ""),
        ac_time_value=data.get("ac_time_value", ""),
        buvid3=data.get("buvid3", ""),
        buvid4=data.get("buvid4", ""),
        dedeuserid=data.get("dedeuserid", ""),
    )


def _prefer_subtitle(subtitles: list[dict]) -> dict:
    def rank(item: dict) -> tuple[int, str]:
        language = str(item.get("lan", "")).lower()
        name = str(item.get("lan_doc", "")).lower()
        preferred = any(token in language or token in name for token in ("zh", "中文", "汉语"))
        return (0 if preferred else 1, language)

    return sorted(subtitles, key=rank)[0]


async def fetch_bilibili(value: str, credential_path: Path) -> BiliResult:
    bvid = extract_bvid(value)
    page_number = _page_number(value)
    video = bili_video.Video(bvid=bvid, credential=_credential(credential_path))
    info = await video.get_info()
    pages = await video.get_pages()
    if page_number > len(pages):
        raise ValueError(f"视频只有 {len(pages)} 个分 P，不能选择 P{page_number}")
    page = pages[page_number - 1]
    player = await video.get_player_info(cid=page["cid"])
    subtitles = (player.get("subtitle") or {}).get("subtitles") or []
    if not subtitles:
        raise RuntimeError("此视频没有可用 CC 字幕；融合版按要求不会回退到 faster-whisper")
    selected = _prefer_subtitle(subtitles)
    url = selected["subtitle_url"]
    if url.startswith("//"):
        url = "https:" + url
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
            response.raise_for_status()
            subtitle = await response.json(content_type=None)
    canonical_url = f"https://www.bilibili.com/video/{bvid}"
    if page_number > 1:
        canonical_url += f"?p={page_number}"
    units = units_from_bilibili_body(subtitle.get("body") or [], canonical_url)
    metadata = {
        "source_type": "bilibili_cc",
        "url": canonical_url,
        "bvid": bvid,
        "page": page_number,
        "cid": page.get("cid"),
        "part": page.get("part", ""),
        "title": info.get("title") or bvid,
        "uploader": (info.get("owner") or {}).get("name", ""),
        "description": info.get("desc", ""),
        "duration": page.get("duration") or info.get("duration"),
        "published_at": info.get("pubdate"),
        "subtitle_language": selected.get("lan_doc") or selected.get("lan"),
        "subtitle_url": url,
    }
    return BiliResult(units=units, metadata=metadata)
