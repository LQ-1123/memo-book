"""端到端冒烟测试：验证 检索→问答 全链路（需要服务已启动且 .env 已填 key）。

用法：
    .venv/bin/python scripts/smoke_test.py --token <你的API_KEY>
    # 服务不在默认地址时加 --base-url http://192.168.x.x:8787

流程：检查 /health → 向监听目录写入测试文件 → 轮询直到可检索 → /ask 验证
引用回答 → 删除测试文档与文件。
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

import httpx

MARK = "个人知识库冒烟测试" + uuid.uuid4().hex[:8]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8790")
    parser.add_argument("--token", required=True)
    parser.add_argument("--timeout", type=int, default=60, help="等待入库超时秒数")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    h = {"X-API-Key": args.token}
    client = httpx.Client(timeout=60)

    # 1. 健康检查（确认连的是本知识库服务，防止打错端口拿到别的服务的响应）
    try:
        resp = client.get(f"{base}/api/v1/health")
    except httpx.HTTPError as e:
        print(f"!! 无法连接 {base}：{e}。请先启动服务：.venv/bin/python -m app.main")
        return 1
    if resp.status_code != 200 or "qdrant" not in (resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}):
        print(f"!! {base} 上的服务不是 personal-library（/api/v1/health 响应异常: {resp.status_code}）。"
              "检查 --base-url 或 .env 里的 APP_PORT")
        return 1
    health = resp.json()
    print(f"[1/5] health: qdrant={health['qdrant']} embed={health['embed_configured']} "
          f"llm={health['llm_configured']} watching={health['watching']}")
    if not (health["embed_configured"] and health["llm_configured"]):
        print("!! .env 中 EMBED_API_KEY / LLM_API_KEY 未配置，无法验证向量与问答链路")
        return 1
    if not health["watch_dirs"]:
        print("!! .env 中 WATCH_DIRS 未配置")
        return 1

    # 2. 写入测试文件
    watch = Path(health["watch_dirs"][0])
    target = watch / f"__smoke_{MARK[:20]}.md"
    target.write_text(
        f"# {MARK}\n\n知识库系统的冒烟测试标记词是 ZEBRA-CANNON-42，"
        "用于验证监听入库、混合检索与问答引用链路。\n",
        encoding="utf-8",
    )
    print(f"[2/5] 已写入测试文件: {target.name}，等待入库…")

    # 3. 轮询直到检索到
    deadline = time.time() + args.timeout
    doc_id = None
    while time.time() < deadline:
        res = client.get(
            f"{base}/api/v1/search", params={"q": "ZEBRA-CANNON-42"}, headers=h
        ).json()
        if res["results"]:
            doc_id = res["results"][0]["document"]["id"]
            print(f"[3/5] 检索命中（{len(res['results'])} 条）doc={doc_id[:8]}…")
            break
        time.sleep(1.5)
    if not doc_id:
        print("!! 超时：未能检索到测试文件，检查服务日志")
        target.unlink(missing_ok=True)
        return 1

    # 4. 问答（应带引用）
    ans = client.post(
        f"{base}/api/v1/ask",
        json={"question": f"{MARK} 的标记词是什么？"},
        headers=h,
    ).json()
    ok = "ZEBRA-CANNON-42" in ans.get("answer", "") and ans.get("sources")
    print(f"[4/5] 问答回答: {ans.get('answer', '')[:120]}")
    print(f"      引用来源: {len(ans.get('sources', []))} 条 → {'通过' if ok else '未通过'}")

    # 5. 清理
    client.delete(f"{base}/api/v1/documents/{doc_id}", headers=h)
    target.unlink(missing_ok=True)
    print("[5/5] 已清理测试文档与文件")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
