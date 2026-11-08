"""问答 Agent（V4）：RAG 检索合同原文 → DeepSeek 生成带引用的回答。

流程：问题 → 混合召回（向量 + BM25）→ 拼证据 → 模型只依据证据作答并标注来源。
无证据时明说「未检索到相关条款」，不编造。
回答用自然语言（像同事当面解释），不使用 Markdown 标记；前端按段落渲染。
"""
from __future__ import annotations

import argparse
import re
from typing import Any

from app.rag import RagEngine, format_evidence

QA_SYSTEM = (
    "你是江山云墅项目的成本合同问答助手。\n"
    "规则：\n"
    "1. 只能依据用户提供的【合同条款原文】作答，不得使用外部知识、不得编造；"
    "原文没有的信息，明确说「合同原文未提及」。\n"
    "2. 用自然语言回答：像同事当面解释一样写 2–4 段连贯的话，先给结论，再补充依据和例外。\n"
    "3. 不要使用任何 Markdown 标记（不要 #、**、-、1.、---、代码块），不要分点罗列。\n"
    "4. 引用依据时用合同编号与条款名自然带出，例如「按《JA-2025-010-04》第八条，……」；"
    "不要用 [1][2] 这类编号。\n"
    "5. 不要在结尾另附「引用合同编号」清单，系统会自动展示引用来源。\n"
    "6. 用简体中文，语气平实，总长度控制在 500 字以内。"
)

# 模型偶尔仍会输出 Markdown 标记：这里做一层兜底清洗，保证界面是自然语言。
_MD_HEAD = re.compile(r"^\s*#{1,6}\s*")
_MD_HR = re.compile(r"^\s*([-*_]\s*){3,}$")
_MD_BULLET = re.compile(r"^\s*[-*+•]\s+")
_MD_ORDER = re.compile(r"^\s*\d+[.)、]\s*")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`\n]+)`")
_MD_QUOTE = re.compile(r"^\s*>\s*")
_MD_REF = re.compile(r"\s*\[\d+\]")   # 模型偶发的 [1][2] 编号引用 → 去掉（来源由界面引用标签展示）


def naturalize_answer(text: str) -> str:
    """把回答里的 Markdown 标记洗掉，保留段落结构（自然语言展示用）。"""
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            lines.append("")
            continue
        if _MD_HR.match(line):
            lines.append("")
            continue
        line = _MD_QUOTE.sub("", line)
        line = _MD_HEAD.sub("", line)
        line = _MD_BULLET.sub("", line)
        line = _MD_ORDER.sub("", line)
        line = _MD_BOLD.sub(r"\1", line)
        line = _MD_ITALIC.sub(r"\1", line)
        line = _MD_CODE.sub(r"\1", line)
        line = _MD_REF.sub("", line)
        lines.append(line.strip())
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def build_context(evidence: list[dict[str, Any]]) -> str:
    parts = []
    for i, e in enumerate(evidence, 1):
        parts.append(f"[{i}] 来源 {e['citation']}：\n{e['text']}")
    return "\n\n".join(parts)


def ask(question: str, rag: RagEngine, model, top_k: int = 5, max_tokens: int = 1200) -> dict[str, Any]:
    hits = rag.retrieve(question, top_k=top_k)
    evidence = format_evidence(hits)
    if not evidence:
        return {"question": question,
                "answer": "未在已入库的合同文本中检索到相关条款。",
                "citations": [], "evidence": []}

    user = (f"用户问题：{question}\n\n"
            f"以下是检索到的合同条款原文：\n\n{build_context(evidence)}\n\n"
            f"请依据以上原文，用自然语言回答（不要 Markdown、不要分点、不要 [1] 编号），"
            f"引用依据时带上合同编号与条款名。")
    try:
        resp = model.complete(QA_SYSTEM, [{"role": "user", "content": user}],
                              tools=[], max_tokens=max_tokens)
        answer = naturalize_answer(resp["text"])
    except Exception as exc:  # noqa: BLE001 —— 模型失败时返回证据供人工查阅
        answer = f"（模型调用失败：{exc}）以下是检索到的合同原文，供人工查阅。"

    return {"question": question, "answer": answer,
            "citations": [e["citation"] for e in evidence],
            "evidence": [{"citation": e["citation"], "text": e["text"]} for e in evidence]}


def main(argv: list[str] | None = None) -> int:
    from app.config import load_settings
    from app.embeddings import make_embedder
    from app.llm import ModelClient
    from app.vectorstore import VectorStore

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question", nargs="+", help="要问的问题")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--no-model", action="store_true", help="只检索证据，不调用模型")
    args = ap.parse_args(argv)

    question = " ".join(args.question)
    settings = load_settings()
    rag = RagEngine(make_embedder(settings),
                    VectorStore(settings.db_path.with_name("buildquote_vectors.db")))

    hits = rag.retrieve(question, top_k=args.top_k)
    evidence = format_evidence(hits)
    if args.no_model:
        print(f"问：{question}\n")
        print("检索到的合同条款（未调用模型）：")
        for e in evidence:
            print(f"  {e['citation']}  {e['text'][:60]}")
        return 0

    out = ask(question, rag, ModelClient(settings), top_k=args.top_k)
    print(f"问：{out['question']}\n")
    print(f"答：{out['answer']}\n")
    if out["citations"]:
        print("引用来源：")
        for c in out["citations"]:
            print("  " + c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
