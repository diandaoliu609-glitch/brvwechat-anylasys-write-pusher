# -*- coding: utf-8 -*-
"""
公众号草稿箱【多图文】推送工具（头条 + 次条，一次 draft/add 推多篇）

为什么单独一个脚本：
  微信 draft/add 的 articles 是数组，第一篇=头条、其后=次条，一次请求即成一稿多文；
  若分两次 draft/add 会变成两条独立单图文草稿，公众号无法合成一条多图文。
  单篇场景仍用 push_draft.py（更短、行为不变），本脚本只处理 N>=2 的多图文。

用法：
  python push_draft_multi.py --account 我的公众号 --config items.json

items.json（数组顺序即头条→次条顺序，最多 8 篇）：
  [
    {"html": "D:/产出/article1.html", "title": "头条标题",
     "digest": "≤100字摘要", "author": "", "cover": "", "source_url": "",
     "original_title": "原标题"},
    {"html": "D:/产出/article2.html", "title": "次条标题", "digest": "..."}
  ]
  只有 html 与 title 必填：
    author      → 依次取 json 指定 → 同目录 article_info.json 的 author → 账号配置的 author
    source_url  → 依次取 json 指定 → 同目录 article_info.json 的 source_url
    cover       → 依次取 json 指定 → 正文第一张本地图 → 自动生成的纯色封面
    digest      → 未填时用 original_title，再没有取正文前 54 字（建议显式给）

凭证来源与 push_draft.py 一致：skill 目录外的私有配置 > 隐藏式交互输入。
复用 push_draft.py 的 token / 上传 / HTML 清洗实现，样式与单篇推送完全一致。
"""
import argparse
import json
import os
import re
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import push_draft as pd  # noqa: E402  (复用单篇脚本的全部实现)


def build_article(token, item, account_name, cfg, idx, total):
    html_path = os.path.abspath(item["html"])
    if not os.path.exists(html_path):
        print("[错误] 第%d篇 HTML 不存在：%s" % (idx + 1, html_path))
        sys.exit(1)
    base_dir = os.path.dirname(html_path)
    title = item["title"]
    pos = "头条" if idx == 0 else "次条%d" % (idx + 1)

    print("-" * 52)
    print("[%s] %s《%s》" % (pos, "处理中", title))

    info = {}
    info_path = os.path.join(base_dir, "article_info.json")
    if os.path.exists(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            info = {}

    author = item.get("author") or info.get("author", "") or cfg.get("author", account_name)
    author = author[:8]  # 微信 author 限 8 字，超长报 45110
    source_url = item.get("source_url") or info.get("source_url", "")

    content = pd.load_article_html(html_path)
    # 封面前置：必须在正文图被替换成 mmbiz 远程 URL 之前锁定本地图
    first_local_img = pd.extract_first_image(content, base_dir)
    content = pd.process_images(token, content, base_dir)

    cover_path = item.get("cover") or ""
    if not (cover_path and os.path.exists(cover_path)):
        cover_path = first_local_img or pd.make_default_cover(title)
    if not cover_path or not os.path.exists(cover_path):
        print("[错误] 第%d篇找不到可用封面，请在 items.json 中用 cover 指定" % (idx + 1))
        sys.exit(1)
    thumb_media_id = pd.upload_cover_material(token, cover_path)

    digest = item.get("digest", "")
    if not digest:
        digest = item.get("original_title", "")[:120]
    if not digest:
        plain = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", content))
        digest = plain[:54]

    print("   正文 %d 字符 | 封面 %s | 作者 %s" % (len(content), os.path.basename(cover_path), author))
    print("   摘要 %s" % digest)

    return {
        "title": title,
        "author": author,
        "digest": digest,
        "content": content,
        "content_source_url": source_url,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }


def main():
    ap = argparse.ArgumentParser(description="多图文（头条+次条）推送到微信公众号草稿箱")
    ap.add_argument("--account", default="", help="公众号名（accounts.json 的键）")
    ap.add_argument("--accounts-file", default="", help="skill 目录外的私有配置文件路径")
    ap.add_argument("--config", required=True, help="多图文配置 JSON 文件路径")
    ap.add_argument("--media-id", default="", help="已存在草稿的 media_id；传入则整体更新该草稿")
    args = ap.parse_args()

    accounts_path = args.accounts_file or pd.DEFAULT_ACCOUNTS_PATH

    account = args.account
    if not account:
        accounts = pd.load_accounts(accounts_path)
        if len(accounts) == 1:
            account = next(iter(accounts))
            print("[提示] 未指定 --account，自动使用配置中唯一的公众号「%s」" % account)
        else:
            print("[错误] 请用 --account 指定公众号。")
            names = "、".join(accounts.keys()) if accounts else "(空，见下方配置引导)"
            print("[提示] 配置文件 %s 中的账号：%s" % (accounts_path, names))
            sys.exit(1)

    cfg = pd.resolve_credentials(account, accounts_path)

    with open(args.config, "r", encoding="utf-8") as f:
        items = json.load(f)
    if not isinstance(items, list) or len(items) < 1:
        print("[错误] config 必须是非空数组")
        sys.exit(1)
    if len(items) > 8:
        print("[错误] 微信单条多图文最多 8 篇，当前 %d 篇" % len(items))
        sys.exit(1)

    print("=" * 52)
    print("[%s] 多图文推送 %d 篇  %s" % (account, len(items), time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 52)

    token = pd.get_token(cfg)
    print("[1] access_token 就绪")

    articles = [build_article(token, it, account, cfg, i, len(items)) for i, it in enumerate(items)]
    print("-" * 52)
    print("[2] %d 篇正文与封面全部就绪" % len(articles))

    if args.media_id:
        url = "https://api.weixin.qq.com/cgi-bin/draft/update?access_token=" + token
        payload = json.dumps({"media_id": args.media_id, "index": 0,
                              "articles": articles}, ensure_ascii=False).encode("utf-8")
    else:
        url = "https://api.weixin.qq.com/cgi-bin/draft/add?access_token=" + token
        payload = json.dumps({"articles": articles}, ensure_ascii=False).encode("utf-8")

    d = pd.http_json(url, data=payload, headers={"Content-Type": "application/json; charset=utf-8"})
    if d.get("media_id") or d.get("errcode", 0) == 0:
        print("[3] 草稿%s成功！media_id = %s" % ("更新" if args.media_id else "创建", d.get("media_id", args.media_id)))
        for i, a in enumerate(articles):
            print("    %s《%s》" % ("头条" if i == 0 else "次条%d" % (i + 1), a["title"]))
        print(">>> 请到 mp.weixin.qq.com → 草稿箱 查看这条多图文")
    else:
        print("[3] 草稿推送失败：", json.dumps(d, ensure_ascii=False))
        if d.get("errcode") == 40164:
            print(">>> IP白名单问题，按报错提示把新IP加进白名单后重试即可")
        sys.exit(1)


if __name__ == "__main__":
    main()
