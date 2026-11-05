"""QA 评测：真实合同金标准 → 召回准确率（top-k 命中正确合同）+ 引用可追溯率。

用法：PYTHONPATH=. .venv/bin/python scripts/eval_qa.py [--k 5] [--ask 3]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    from app.embeddings import Embedder, LocalModelEmbedding
    from app.rag import RagEngine, format_evidence
    from app.vectorstore import VectorStore

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--ask", type=int, default=0, help="抽样几题调模型测引用可追溯率")
    args = ap.parse_args(argv)

    rag = RagEngine(Embedder(LocalModelEmbedding("BAAI/bge-small-zh-v1.5")),
                    VectorStore(ROOT / "data" / "buildquote_vectors.db"))
    samples = json.loads((ROOT / "evals" / "qa_gold.json").read_text(encoding="utf-8"))["samples"]

    hit = 0
    print(f"== 召回评测（top-{args.k} 是否命中正确合同）==\n")
    for s in samples:
        hits = rag.retrieve(s["question"], top_k=args.k)
        pred = [h["source_id"] for h in hits]
        ok = s["contract"] in pred
        hit += ok
        mark = "✓" if ok else "✗"
        print(f"  {mark} {s['contract']:<14} {s['question']}  → 命中 {pred[0] if pred else '—'}")
    recall = hit / len(samples)
    print(f"\n召回准确率（top-{args.k}）：{hit}/{len(samples)} = {recall:.1%}")

    if args.ask > 0:
        from app.config import load_settings
        from app.llm import ModelClient
        from app.qa import ask

        settings = load_settings()
        model = ModelClient(settings)
        print(f"\n== 引用可追溯率（抽样 {args.ask} 题调模型）==\n")
        cited = 0
        for s in samples[:args.ask]:
            out = ask(s["question"], rag, model, top_k=args.k)
            has_cite = bool(out["citations"])
            cited += has_cite
            print(f"  {'✓' if has_cite else '✗'} {s['question']}")
            print(f"      引用：{out['citations']}")
            print(f"      答：{out['answer'][:90].replace(chr(10), ' ')}")
        print(f"\n引用可追溯率：{cited}/{args.ask} = {cited/args.ask:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
