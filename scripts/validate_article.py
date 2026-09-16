#!/usr/bin/env python3
"""Validate a WeChat article manifest before any draft API mutation."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


RULES = {
    "政策雷达": {"min_chars": 1200, "max_chars": 2200, "min_sources": 2, "min_ab": 2},
    "智瞰深一度": {"min_chars": 1500, "max_chars": 2600, "min_sources": 2, "min_ab": 2},
    "工程实战": {"min_chars": 1600, "max_chars": 2800, "min_sources": 3, "min_ab": 2},
    "企业与案例": {"min_chars": 1500, "max_chars": 2400, "min_sources": 3, "min_ab": 2},
    "产品观察": {"min_chars": 1400, "max_chars": 2200, "min_sources": 3, "min_ab": 2},
    "一周低空情报": {"min_chars": 900, "max_chars": 1600, "min_sources": 3, "min_ab": 2},
}

REQUIRED_PHRASES = ("更新时间", "数据来源", "免责声明", "接下来")
COLUMN_REQUIRED_PHRASES = {
    "工程实战": ("复现条件", "参数边界", "适用场景", "失效条件", "开源许可"),
    "企业与案例": ("项目阶段", "证据边界"),
    "产品观察": ("参数口径", "适用场景", "限制条件"),
}
UNRESOLVED_MARKERS = ("待补", "待核验", "TODO", "TBD", "据公开报道")
RISKY_CLAIMS = (
    "全面放开", "强制倒计时", "稳赚", "必然上涨", "无风险", "内部消息",
    "全国第一", "全网唯一", "已经商用", "即将商用",
)


def utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", "", value)


def validate(manifest: dict) -> list[str]:
    errors: list[str] = []
    for field in ("account", "column", "title", "digest", "text", "html", "sources", "issues"):
        if field not in manifest:
            errors.append(f"缺少字段：{field}")
    if errors:
        return errors

    if manifest["account"] != "低空智瞰":
        errors.append("账号必须是低空智瞰")

    column = manifest["column"]
    rule = RULES.get(column)
    if not rule:
        errors.append(f"不支持的自动栏目：{column}")
        return errors

    title = str(manifest["title"]).strip()
    digest = str(manifest["digest"]).strip()
    text = str(manifest["text"])
    html = str(manifest["html"])
    count = len(plain_text(text))

    if not title or utf8_len(title) > 64:
        errors.append("标题为空或超过 64 个 UTF-8 字节")
    if not digest or len(digest) > 120:
        errors.append("摘要为空或超过 120 个汉字")
    if not rule["min_chars"] <= count <= rule["max_chars"]:
        errors.append(f"正文长度 {count}，应为 {rule['min_chars']}–{rule['max_chars']} 汉字")
    if not re.search(r'<div[^>]+id=["\']article["\']', html, flags=re.I):
        errors.append("HTML 缺少 id=article 的正文容器")

    sources = manifest["sources"] if isinstance(manifest["sources"], list) else []
    if len(sources) < rule["min_sources"]:
        errors.append(f"来源不足：至少 {rule['min_sources']} 个")
    ab_count = 0
    seen_hosts: set[str] = set()
    for idx, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            errors.append(f"来源 {idx} 不是对象")
            continue
        tier = str(source.get("tier", "")).upper()
        url = str(source.get("url", ""))
        parsed = urlparse(url)
        if tier in {"A", "B"}:
            ab_count += 1
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"来源 {idx} URL 无效")
        elif parsed.netloc in seen_hosts and len(sources) > 1:
            errors.append(f"来源 {idx} 与其他来源同域，不能算独立来源：{parsed.netloc}")
        seen_hosts.add(parsed.netloc)
        if not source.get("title") or not source.get("date") or tier not in {"A", "B", "C"}:
            errors.append(f"来源 {idx} 缺 title/date 或 tier 非 A/B/C")
    if ab_count < rule["min_ab"]:
        errors.append(f"A/B 级来源不足：至少 {rule['min_ab']} 个")

    issues = manifest["issues"]
    if issues:
        errors.append("仍有未解决 issues：" + "; ".join(map(str, issues)))
    for marker in UNRESOLVED_MARKERS:
        if marker in text:
            errors.append(f"正文含未解决标记：{marker}")
    for phrase in RISKY_CLAIMS:
        if phrase in title or phrase in text:
            errors.append(f"命中高风险表述：{phrase}")
    for phrase in REQUIRED_PHRASES:
        if phrase not in text:
            errors.append(f"正文缺少必要内容：{phrase}")
    for phrase in COLUMN_REQUIRED_PHRASES.get(column, ()):
        if phrase not in text:
            errors.append(f"{column}缺少必要内容：{phrase}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"VALIDATION_FAILED\n- 无法读取清单：{exc}")
        return 1
    errors = validate(manifest)
    if errors:
        print("VALIDATION_FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("VALIDATION_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
