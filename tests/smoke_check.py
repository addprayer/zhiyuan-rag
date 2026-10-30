"""Manual end-to-end smoke check against a running server.

Usage:
    python tests/smoke_check.py
    ZHIYUAN_BASE=http://127.0.0.1:8123 python tests/smoke_check.py
"""

from __future__ import annotations

import json
import os
import sys

import requests

BASE = os.getenv("ZHIYUAN_BASE", "http://127.0.0.1:8000").rstrip("/")


def main() -> int:
    out: list[str] = []
    health = requests.get(f"{BASE}/api/health", timeout=10).json()
    out.append(f"health: {health}")

    kbs = requests.get(f"{BASE}/api/kbs", timeout=10).json()["items"]
    if not kbs:
        out.append("no knowledge base")
        print("\n".join(out))
        return 1
    kb_id = kbs[0]["id"]
    out.append(f"kb: {kbs[0]['name']} docs={kbs[0]['document_count']} chunks={kbs[0]['chunk_count']}")

    search = requests.post(
        f"{BASE}/api/search",
        json={"kb_id": kb_id, "query": "年假有几天", "top_k": 5},
        timeout=30,
    ).json()
    out.append(f"search info: {search['info']}")
    for item in search["items"]:
        out.append(
            f"  [{item['index']}] {item['document_name']} p={item['page']} "
            f"score={item['score']} vec={item['vector_score']} lex={item['lexical_score']}"
        )
        out.append(f"      {item['snippet'][:100]}")

    out.append("chat stream:")
    with requests.post(
        f"{BASE}/api/chat",
        json={"kb_id": kb_id, "question": "年假有几天？可以转到明年吗？"},
        stream=True,
        timeout=60,
    ) as response:
        event = ""
        for raw in response.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if event == "delta":
                    out.append(payload["text"])
                else:
                    out.append(f"  <{event}> {json.dumps(payload, ensure_ascii=False)[:300]}")

    test_embed = requests.post(
        f"{BASE}/api/settings/test-embedding", json={}, timeout=30
    ).json()
    out.append(f"embedding: {test_embed}")

    text = "\n".join(out)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
