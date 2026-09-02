"""金标准问答集检索命中评估：逐题调 /search，统计 hit@k（需服务已启动）。

用法：
    .venv/bin/python scripts/eval_rag.py --token <你的API_KEY>
    # 服务不在默认地址时加 --base-url http://192.168.x.x:8790
题集：data/golden_questions.json（question + expected 标题子串，不区分大小写），自行维护。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

GOLDEN = Path(__file__).resolve().parent.parent / "data" / "golden_questions.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索金标准评估")
    parser.add_argument("--base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--token", required=True)
    parser.add_argument("--topk", type=int, default=5, help="判定的 top-k（默认 5）")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    client = httpx.Client(timeout=60)

    # 健康检查（确认连的是本知识库服务，防止打错端口拿到别的服务的响应）
    try:
        resp = client.get(f"{base}/api/v1/health")
    except httpx.HTTPError as e:
        print(f"!! 无法连接 {base}：{e}。请先启动服务")
        return 1
    if resp.status_code != 200 or "qdrant" not in resp.json():
        print(f"!! {base} 上的服务不是 personal-library（/api/v1/health 响应异常）")
        return 1

    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    hits = 0
    for c in cases:
        try:
            r = client.get(
                f"{base}/api/v1/search",
                params={"q": c["question"], "topk": args.topk},
                headers={"X-API-Key": args.token},
            )
            results = r.json().get("results", [])
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {c['question']}  请求失败: {e}")
            continue
        want = c["expected"].lower()
        ok = any(want in (it["document"]["title"] or "").lower() for it in results)
        hits += ok
        top = results[0]["document"]["title"][:30] if results else "（无结果）"
        print(f"  {'✓' if ok else '✗'} {c['question']}  → top1: {top}")

    n = len(cases)
    print(f"\nhit@{args.topk}: {hits}/{n} = {hits / n:.0%}")
    return 0 if hits == n else 1


if __name__ == "__main__":
    sys.exit(main())
