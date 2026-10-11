from __future__ import annotations

import json
import re
import threading
from http.cookies import SimpleCookie
from typing import Any

from opencc import OpenCC

SIMPLIFIED = "zh-Hans"
TRADITIONAL = "zh-Hant"
SUPPORTED_LANGUAGES = {SIMPLIFIED, TRADITIONAL}

_thread_state = threading.local()


def language_from_cookie(cookie_header: str | None) -> str:
    """Return the persisted display language without trusting arbitrary cookie values."""
    if not cookie_header:
        return SIMPLIFIED
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return SIMPLIFIED
    value = cookie.get("fusion_language")
    language = value.value if value else ""
    return language if language in SUPPORTED_LANGUAGES else SIMPLIFIED


def _converter(language: str) -> OpenCC:
    mode = "s2t" if language == TRADITIONAL else "t2s"
    converters = getattr(_thread_state, "converters", None)
    if converters is None:
        converters = {}
        _thread_state.converters = converters
    if mode not in converters:
        converters[mode] = OpenCC(mode)
    return converters[mode]


def convert_text(value: str, language: str) -> str:
    if language not in SUPPORTED_LANGUAGES:
        language = SIMPLIFIED
    return _converter(language).convert(value)


def convert_payload(value: Any, language: str) -> Any:
    """Convert display values while preserving API field names and data types."""
    if isinstance(value, str):
        return convert_text(value, language)
    if isinstance(value, list):
        return [convert_payload(item, language) for item in value]
    if isinstance(value, tuple):
        return [convert_payload(item, language) for item in value]
    if isinstance(value, dict):
        return {key: convert_payload(item, language) for key, item in value.items()}
    return value


def _toggle_markup(language: str, *, floating: bool) -> str:
    button = ""
    style = ""
    if floating:
        style = """
<style id="fusion-language-style">
#fusion-language-toggle.fusion-language-floating{position:fixed;right:16px;top:16px;z-index:2147483647;
border:1px solid #bccbce;border-radius:999px;padding:8px 12px;background:#fffffff2;color:#244955;
font:700 12px/1.2 system-ui,"Microsoft YaHei",sans-serif;box-shadow:0 7px 24px #1e33431f;cursor:pointer}
#fusion-language-toggle.fusion-language-floating:hover{border-color:#28738b;color:#155e77}
</style>"""
        button = (
            '<button id="fusion-language-toggle" class="fusion-language-floating" '
            'type="button" hidden></button>'
        )
    current = json.dumps(language)
    script = f"""
<script data-fusion-language-bootstrap>
(()=>{{
  const current={current};
  const button=document.getElementById('fusion-language-toggle');
  document.documentElement.lang=current;
  if(!button)return;
  if(window.top!==window){{button.hidden=true;return;}}
  button.hidden=false;
  const target=current==='zh-Hant'?'zh-Hans':'zh-Hant';
  button.textContent=target==='zh-Hant'?'繁體':'简体';
  button.title=target==='zh-Hant'?'切换为繁体中文':'切換為簡體中文';
  button.setAttribute('aria-label',button.title);
  button.addEventListener('click',()=>{{
    try{{
      document.cookie=`fusion_language=${{target}}; Path=/; Max-Age=31536000; SameSite=Lax`;
    }}catch(_error){{
      // CSP-sandboxed artifact pages cannot write cookies; the URL still works.
    }}
    const destination=new URL(location.href);
    destination.searchParams.set('lang',target);
    location.assign(destination.toString());
  }});
}})();
</script>"""
    return f"{style}{button}{script}"


def _disable_legacy_tree_detail_popup(document: str) -> str:
    """Hide obsolete node-detail UI in already generated compact trees.

    The capture listener suppresses the old popup handler while forwarding the
    same click to the report locator. Saved artifacts stay immutable.
    """
    if 'id="detail-panel"' not in document or "data-node-select" not in document:
        return document
    if "data-fusion-disable-tree-detail" in document:
        return document
    document = document.replace(
        "选择左侧节点可查看详情并定位报告",
        "选择左侧节点可定位到报告内容",
    )
    override = """
<style data-fusion-disable-tree-detail>#detail-panel{display:none!important}</style>
<script data-fusion-disable-tree-detail>
document.addEventListener('click',event=>{
  const target=event.target;
  const node=target instanceof Element?target.closest('[data-node-select]'):null;
  if(!node)return;
  event.stopImmediatePropagation();
  if(node.dataset.canLocate==='true'&&typeof window.locate==='function')window.locate(node);
},true);
</script>"""
    position = document.lower().rfind("</body>")
    if position >= 0:
        return document[:position] + override + document[position:]
    return document + override


def convert_html_document(document: str, language: str) -> str:
    """Localize a served HTML copy and add a top-level one-click switch."""
    # Older saved trees displayed a now-retired "logic type" badge. Keep the
    # source artifacts immutable, but remove that badge from every served copy.
    document = re.sub(
        r'<span class="root-mark">(?:逻辑型|邏輯型)</span>',
        "",
        document,
        flags=re.IGNORECASE,
    )
    document = re.sub(
        r'(<div class="root-card"[^>]*>)<strong>(?:逻辑型|邏輯型)</strong><small>(.*?)</small>(</div>)',
        r"\1\2\3",
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    document = _disable_legacy_tree_detail_popup(document)
    localized = convert_text(document, language)
    localized = re.sub(
        r'(<html\b[^>]*\blang=")[^"]*(")',
        rf"\g<1>{language}\g<2>",
        localized,
        count=1,
        flags=re.IGNORECASE,
    )
    # Sandboxed report iframes have an opaque origin and may not receive the
    # preference cookie. Carry the language explicitly on local HTML embeds.
    def embed_language(match: re.Match[str]) -> str:
        prefix, url, suffix = match.groups()
        if re.search(r"(?:^|[?&])lang=(?:zh-Hans|zh-Hant)(?:&|$)", url):
            return match.group(0)
        separator = "&" if "?" in url else "?"
        return f"{prefix}{url}{separator}lang={language}{suffix}"

    localized = re.sub(
        r'(\bsrc\s*=\s*["\'])(?!https?:|//|data:)([^"\']+\.html(?:\?[^"\']*)?)(["\'])',
        embed_language,
        localized,
        flags=re.IGNORECASE,
    )
    if "data-fusion-language-bootstrap" in localized:
        return localized
    floating = 'id="fusion-language-toggle"' not in localized
    markup = _toggle_markup(language, floating=floating)
    if "</body>" in localized.lower():
        position = localized.lower().rfind("</body>")
        return localized[:position] + markup + localized[position:]
    return localized + markup
