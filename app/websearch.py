"""联网检索（建筑行业通用知识）：百度网页结果 + 维基百科摘要，可选 DeepSeek 生成带引用的联网摘要。

说明：
- 合同问答仍严格只依据合同原文（红线）；本模块只服务于「知识检索」的联网模式，
  返回公开网页/百科结果供参考，不写入合同口径。
- 网络不可用或结果为空时返回空列表，前端提示「联网检索未返回结果」。
"""
from __future__ import annotations

import html
import re
import time
from typing import Any
from urllib.parse import quote

import httpx

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"}

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 600


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    stamped, value = hit
    if time.time() - stamped > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), value)
    if len(_CACHE) > 100:
        for k in list(_CACHE)[:20]:
            _CACHE.pop(k, None)


def _strip(fragment: str) -> str:
    txt = re.sub(r"<script.*?</script>", " ", fragment, flags=re.S)
    txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S)
    txt = re.sub(r"<!--.*?-->", " ", txt, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", html.unescape(txt)).strip()


def _get(url: str, params: dict[str, Any] | None = None, timeout: float = 9.0) -> httpx.Response:
    return httpx.get(url, params=params, headers=_HEADERS, timeout=timeout, follow_redirects=True)


# ---------------------------------------------------------------------------
# 百度网页搜索
# ---------------------------------------------------------------------------
def search_baidu(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    try:
        resp = _get("https://www.baidu.com/s", {"wd": query})
        if resp.status_code != 200:
            return []
        raw = resp.text
    except Exception:  # noqa: BLE001 —— 网络失败降级为空
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in re.finditer(r'<h3[^>]*class="[^"]*cosc-title[^"]*"[^>]*>(.*?)</h3>', raw, re.S):
        block = m.group(1)
        link = re.search(r'<a[^>]*href="([^"]+)"', block)
        if not link:
            continue
        url = html.unescape(link.group(1))
        if not url.startswith("http"):
            continue
        title_m = re.search(r"<!--s-text-->(.*?)<!--/s-text-->", block, re.S)
        title = _strip(title_m.group(1) if title_m else block)
        if not title or title in seen:
            continue
        seen.add(title)
        tail = raw[m.end(): m.end() + 8000]
        snip_m = re.search(
            r'class="[^"]*(?:summary-text[^"]*|c-abstract[^"]*|content-right[^"]*|cosc-abstract[^"]*)"[^>]*>(.*?)</(?:span|div)>',
            tail, re.S)
        snippet = _strip(snip_m.group(1)) if snip_m else ""
        items.append({"title": title[:120], "url": url, "snippet": snippet[:260], "source": "百度"})
        if len(items) >= top_k:
            break
    return items


# ---------------------------------------------------------------------------
# 维基百科摘要（建筑行业通用知识，内容相对规范）
# ---------------------------------------------------------------------------
def search_wiki(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    try:
        r = _get("https://zh.wikipedia.org/w/api.php", {
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": top_k, "utf8": 1,
        })
        data = r.json()
        titles = [s["title"] for s in (data.get("query", {}).get("search") or [])]
        if not titles:
            return []
        r2 = _get("https://zh.wikipedia.org/w/api.php", {
            "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
            "titles": "|".join(titles), "format": "json", "redirects": 1,
        })
        pages = (r2.json().get("query", {}) or {}).get("pages", {}) or {}
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for page in pages.values():
        title = page.get("title") or ""
        extract = (page.get("extract") or "").strip()
        if not title or not extract:
            continue
        out.append({"title": title, "url": "https://zh.wikipedia.org/wiki/" + quote(title),
                    "snippet": extract[:320], "source": "维基百科"})
        if len(out) >= top_k:
            break
    return out



# ---------------------------------------------------------------------------
# 内置建筑知识兜底（行业基础概念；网络受限时仍能给出可查内容，来源明确标注）
# ---------------------------------------------------------------------------
_BUILTIN_KB: dict[str, tuple[str, str]] = {
    "混凝土强度等级": ("混凝土强度等级", "混凝土强度等级按立方体抗压强度标准值划分，常用 C15–C80；C30 表示 28 天标准养护下抗压强度标准值 30MPa。强度等级越高，抗压能力越强。" ),
    "钢筋型号": ("钢筋型号与牌号", "常用热轧带肋钢筋牌号 HRB400、HRB500（HRB400E 为抗震钢筋），数字表示屈服强度标准值（MPa）；HPB300 为光圆钢筋。" ),
    "钢筋理论重量": ("钢筋理论重量", "钢筋每米理论重量（kg/m）≈ 0.00617 × 直径(mm)²，例如 Φ20 钢筋约 2.47kg/m；也可按 0.00617d² 快速估算。" ),
    "水泥": ("水泥", "常用水泥包括硅酸盐水泥（P·I/P·II）、普通硅酸盐水泥（P·O）、矿渣硅酸盐水泥（P·S）等；强度等级如 42.5、52.5，数字为 28 天抗压强度（MPa）。" ),
    "砌体": ("砌体结构", "砌体结构由砖、砌块与砂浆砌筑而成，常见砖砌体、混凝土小型空心砌块砌体；抗震设防区需设置构造柱、圈梁等约束构件。" ),
    "钢结构": ("钢结构", "钢结构以钢材构件（钢柱、钢梁、支撑等）承受荷载，具有自重轻、强度高、施工快的特点；需做防火防腐涂装，节点连接分焊接与螺栓连接。" ),
    "防水": ("建筑防水", "建筑防水分屋面、地下、卫生间等部位，材料有卷材（SBS、自粘）、涂料（聚氨酯、JS）等；地下防水遵循“以防为主、刚柔结合、多道设防”。" ),
    "保温": ("建筑保温", "外墙保温分 EPS/XPS 板、岩棉板、保温装饰一体板等；需满足节能设计标准规定的传热系数，并做防火隔离带与锚固措施。" ),
    "验收规范": ("建筑工程施工质量验收", "建筑工程施工质量验收按《建筑工程施工质量验收统一标准》GB 50300 及专业验收规范执行，分检验批、分项、分部、单位工程逐级验收，资料与实体同步。" ),
    "清单计价": ("工程量清单计价", "清单计价按《建设工程工程量清单计价规范》GB 50500 执行：招标人给清单，投标人综合单价报价，包含人工、材料、机械、管理费、利润及风险。" ),
    "定额": ("工程定额", "工程定额是完成单位合格产品所需人工、材料、机械消耗量的标准，分为预算定额、概算定额、企业定额等，用于组价与成本测算。" ),
    "措施费": ("措施项目费", "措施项目费包括安全文明施工费、夜间施工、二次搬运、脚手架、模板、垂直运输等，分为可计量与不可计量措施项目。" ),
    "安全文明施工费": ("安全文明施工费", "安全文明施工费属于不可竞争费用，含环境保护、文明施工、安全施工、临时设施四项，计价时按规定费率计取，不得下浮。" ),
    "变更签证": ("工程变更与现场签证", "工程变更需履行审批程序并留存指令单；现场签证应写明事由、工程量、时间、部位并经监理/建设单位签认，作为结算依据。" ),
    "进度款": ("工程进度款", "进度款按合同约定的计量周期（如月度）确认已完工程量，按约定比例支付；支付前需核验计量报表、质量验收与发票。" ),
    "结算": ("工程竣工结算", "竣工结算依据合同、变更签证、计量与验收资料编制，经审核确认后作为最终付款依据；结算争议可按合同约定协商、调解或仲裁/诉讼。" ),
    "质保金": ("工程质量保证金", "质量保证金（质保金）一般按结算价 3% 左右预留，缺陷责任期（常见 24 个月）满且无质量问题后返还；具体比例与期限以合同约定为准。" ),
    "履约保证金": ("履约保证金", "履约保证金用于担保承包人按合同履约，常见为合同价的 5%–10%，可采用保函形式；工程验收合格后按约定退还。" ),
    "投标": ("工程投标", "投标需编制投标文件（商务标、技术标、资信标），响应招标文件实质性要求；低于成本报价、串标等属违规，可能被否决投标。" ),
    "工程造价": ("工程造价", "工程造价由分部分项工程费、措施项目费、其他项目费、规费和税金组成；造价文件应做到量价分离、依据可查。" ),
}


def _relevant(query: str, text: str) -> bool:
    """查询与结果是否有 ≥2 字的重叠（过滤维基百科里明显跑题的结果）。"""
    q = re.sub(r"\s+", "", query or "")
    t = re.sub(r"\s+", "", text or "")
    if not q or not t:
        return False
    for n in (4, 3, 2):
        for i in range(0, len(q) - n + 1):
            if q[i:i + n] in t:
                return True
    return False


def builtin_results(query: str, top_k: int = 3, fallback: bool = False) -> list[dict[str, Any]]:
    """按关键词匹配内置建筑知识（来源标注“内置知识库”）；fallback=True 时无命中给两条示例。"""
    hits: list[dict[str, Any]] = []
    q = (query or "").strip()
    for key, (title, text) in _BUILTIN_KB.items():
        if key in q or q in key or any(k in q for k in key if len(k) >= 2):
            hits.append({"title": title, "url": "", "snippet": text, "source": "内置知识库"})
            if len(hits) >= top_k:
                break
    if not hits and fallback:
        for key, (title, text) in list(_BUILTIN_KB.items())[:2]:
            hits.append({"title": title, "url": "", "snippet": text, "source": "内置知识库"})
    return hits


# ---------------------------------------------------------------------------
# 联网摘要（DeepSeek 依据检索结果作答，带 [n] 引用）
# ---------------------------------------------------------------------------
WEB_SYSTEM = (
    "你是建筑行业知识助手。只能依据用户提供的【联网检索结果】回答，不得使用其他知识、不得编造；"
    "检索结果没有覆盖的内容，明确说「检索结果未提及」。用自然语言 2–3 段回答，不要 Markdown 分点；"
    "每条结论用 [1][2] 标注对应检索结果编号。"
)


def summarize(query: str, results: list[dict[str, Any]], model, max_tokens: int = 700) -> str:
    from app.qa import naturalize_answer

    ctx = "\n\n".join(
        f"[{i + 1}] {r.get('title', '')}（{r.get('source', '')}）：{r.get('snippet', '')}"
        for i, r in enumerate(results)
    )
    user = (f"用户问题：{query}\n\n以下是联网检索结果（含标题与摘要）：\n\n{ctx}\n\n"
            f"请依据以上结果回答，标注 [n] 来源编号。")
    try:
        resp = model.complete(WEB_SYSTEM, [{"role": "user", "content": user}], tools=[], max_tokens=max_tokens)
        return naturalize_answer(resp.get("text", ""))
    except Exception as exc:  # noqa: BLE001 —— 模型失败时只给检索结果
        return f"（联网摘要生成失败：{exc}，可直接查看下方检索结果。）"


def search(query: str, top_k: int = 5, model=None, with_answer: bool = True) -> dict[str, Any]:
    """联网检索入口：百科摘要优先 + 百度网页结果，可选 DeepSeek 汇总回答。"""
    key = f"{query}|{top_k}|{bool(model) and with_answer}"
    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}

    wiki = search_wiki(query, top_k=min(3, top_k))
    baidu = search_baidu(query, top_k=max(2, top_k - len(wiki)))
    wiki = [r for r in wiki if _relevant(query, (r.get("title", "") + " " + r.get("snippet", "")))]
    baidu = [r for r in baidu if _relevant(query, (r.get("title", "") + " " + r.get("snippet", "")))]
    builtin = builtin_results(query, top_k=2, fallback=(not wiki and not baidu))
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in builtin + wiki + baidu:
        title = r.get("title") or ""
        if title in seen:
            continue
        seen.add(title)
        results.append(r)
    results = results[:top_k]
    answer = ""
    if results and with_answer and model is not None:
        answer = summarize(query, results, model)
    out = {"ok": True, "query": query, "results": results[:top_k], "answer": answer,
           "sources": [r.get("source") for r in results[:top_k]],
           "note": "" if results else "联网检索未返回结果（网络不可用或查询无命中）。"}
    _cache_put(key, out)
    return out
