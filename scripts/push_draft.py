# -*- coding: utf-8 -*-
"""
公众号草稿箱推送工具（通用版，可分发分享）

把本地 HTML 文章推送到微信公众号草稿箱：
  - 单篇   ：python push_draft.py --html 文章.html --title "标题" --account 我的公众号
  - 多图文 ：python push_draft_multi.py --account 我的公众号 --config items.json

凭证来源：
  1. skill 目录外的私有配置文件（用 --accounts-file 或 BRVWECHAT_ACCOUNTS_FILE 指定）
  2. 运行时交互式输入（AppSecret 使用隐藏输入且不会落盘）

凭证绝不写死在脚本或文章里；每个公众号使用各自的 AppID / AppSecret。
"""

import argparse
import getpass
import json
import os
import re
import sys
import time
import urllib.request
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CACHE_HOME = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
DEFAULT_ACCOUNTS_PATH = os.environ.get(
    "BRVWECHAT_ACCOUNTS_FILE",
    os.path.join(CONFIG_HOME, "brvwechat", "accounts.json"),
)
TOKEN_CACHE = os.path.join(CACHE_HOME, "brvwechat", "token_cache.json")

# 通用包不预设默认公众号；用 --account 指定，或按配置文件唯一账号自动选中
DEFAULT_ACCOUNT = ""

# 生成默认封面时的中文字体候选（按平台覆盖 Windows / macOS / Linux）
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def _ask(prompt):
    """交互式询问；非交互环境（stdin 关闭）返回空串，避免阻塞。"""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def load_accounts(path=DEFAULT_ACCOUNTS_PATH):
    """读取账号配置；文件不存在或解析失败时返回空 dict（不崩溃）。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_credentials(account_name, accounts_path=DEFAULT_ACCOUNTS_PATH):
    """
    从外部私有配置读取 AppID / AppSecret；缺失时交互式补全且不落盘。
    """
    accounts = load_accounts(accounts_path)
    cfg = dict(accounts.get(account_name) or {})

    appid = cfg.get("appid", "")
    secret = cfg.get("appsecret", "")

    if not appid or not secret:
        print("=" * 56)
        print("[配置] 未找到公众号「%s」的完整凭证。" % account_name)
        print("       请编辑 skill 目录外的私有配置文件：%s" % accounts_path)
        print('          填入 { "appid": "wx...", "appsecret": "..." }')
        print("       也可仅为本次运行输入；内容不会保存。")
        print("=" * 56)
        if not appid:
            appid = _ask("  AppID: ")
        if not secret:
            try:
                secret = getpass.getpass("  AppSecret: ").strip()
            except (EOFError, KeyboardInterrupt):
                secret = ""

    if not appid or not secret:
        print("[错误] 缺少 AppID / AppSecret，无法获取 access_token。")
        print("       请到 mp.weixin.qq.com → 设置与开发 → 基本配置 查看，")
        print("       然后填入 %s" % accounts_path)
        sys.exit(1)

    cfg["appid"] = appid
    cfg["appsecret"] = secret

    if "author" not in cfg:
        cfg["author"] = account_name
    return cfg


# ---------- HTTP ----------
def http_json(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode("utf-8"))


def get_token(cfg, force=False):
    """带本地缓存的 access_token（按 appid 分账号缓存，有效期2小时，提前5分钟刷新）"""
    appid = cfg["appid"]
    cache = {}
    if not force and os.path.exists(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    if appid in cache and cache[appid].get("expire_at", 0) > time.time() + 300:
        return cache[appid]["token"]
    url = ("https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid="
           + appid + "&secret=" + cfg["appsecret"])
    d = http_json(url)
    if "access_token" not in d:
        print("[错误] 获取access_token失败：", json.dumps(d, ensure_ascii=False))
        if d.get("errcode") == 40164:
            print(">>> IP不在白名单。请到 mp.weixin.qq.com → 设置与开发 → 基本配置 → IP白名单，"
                  "把报错里提示的IP加进去后重试。")
        sys.exit(1)
    token = d["access_token"]
    cache[appid] = {"token": token, "expire_at": time.time() + 7200}
    try:
        os.makedirs(os.path.dirname(TOKEN_CACHE), mode=0o700, exist_ok=True)
        with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        try:
            os.chmod(TOKEN_CACHE, 0o600)
        except OSError:
            pass
    except Exception:
        pass  # 缓存目录不可写时仅本次生效
    return token


# ---------- multipart 上传 ----------
def multipart_upload(url, filepath, field="media", content_type=None):
    boundary = uuid.uuid4().hex
    filename = os.path.basename(filepath)
    ctype = content_type or ("image/png" if filename.lower().endswith(".png") else "image/jpeg")
    with open(filepath, "rb") as f:
        img = f.read()
    body = (
        ("--%s\r\n" % boundary).encode()
        + ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
           "Content-Type: %s\r\n\r\n" % (field, filename, ctype)).encode()
        + img
        + ("\r\n--%s--\r\n" % boundary).encode()
    )
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    resp = urllib.request.urlopen(req, timeout=120)
    return json.loads(resp.read().decode("utf-8"))


def upload_content_image(token, filepath):
    url = "https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token=" + token
    d = multipart_upload(url, filepath)
    if "url" not in d:
        raise RuntimeError("正文图片上传失败 %s: %s" % (os.path.basename(filepath), json.dumps(d, ensure_ascii=False)))
    return d["url"]


def upload_cover_material(token, filepath):
    url = "https://api.weixin.qq.com/cgi-bin/material/add_material?access_token=%s&type=image" % token
    d = multipart_upload(url, filepath)
    if "media_id" not in d:
        raise RuntimeError("封面上传失败: " + json.dumps(d, ensure_ascii=False))
    return d["media_id"]


# ---------- HTML 处理 ----------
def strip_textnode_whitespace(html):
    """删除「文本节点」首尾的换行/缩进空白。

    本地 HTML 为了可读性通常写成：
        <p style="...">\n  正文内容\n</p>
    这些换行与缩进是纯粹的格式空白，但微信编辑器会把它们当成真实空白渲染，
    表现为**每段正文前面多出空格 / 空行**。只处理文本节点（>...< 之间），
    不动标签属性，也不会破坏文本内部的正常空格。
    """
    def repl(m):
        text = m.group(1)
        if not text.strip():
            return "><"           # 纯空白文本节点（标签之间的换行缩进）直接删掉
        return ">" + text.strip() + "<"
    return re.sub(r">([^<]*)<", repl, html)


def extract_article_block(html):
    """提取正文容器 <div id="article">...</div>（div 标签配对，支持内部嵌套）。

    本地预览 HTML 通常在正文外面包了一层「页面外壳」：一键复制按钮、预览提示语
    （如「以上是正文，可一键复制粘贴进公众号编辑器」）等。这些是给作者本地看的，
    **绝不能推送进公众号草稿**。

    优先取 id="article" 区块；若 HTML 无该容器，则退回取 body 并按 class 剥离外壳。
    返回 None 表示没有找到正文容器，调用方应退回整段 body。
    """
    m = re.search(r'<div[^>]*\bid\s*=\s*["\']article["\'][^>]*>', html, flags=re.I)
    if not m:
        return None
    start = m.end()
    depth = 1
    for dm in re.finditer(r'<(/?)div\b[^>]*>', html[start:], flags=re.I):
        depth += -1 if dm.group(1) else 1
        if depth == 0:
            return html[start:start + dm.start()]
    return html[start:]  # 未闭合则取到结尾，总比推外壳强


# 本地预览外壳里常见的提示性元素：没有 id="article" 容器时的兜底剥离名单
SHELL_CLASS_RE = re.compile(
    r'<(?P<tag>p|div|section|span)[^>]*\bclass\s*=\s*["\'][^"\']*'
    r'\b(?:preview-tip|copy-bar|copy-btn|copy-tip|preview-note|page-tip)\b[^"\']*["\'][^>]*>'
    r'[\s\S]*?</(?P=tag)>',
    flags=re.I)


def load_article_html(html_path):
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r"<button[\s\S]*?</button>", "", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.I)
    html = re.sub(r"<textarea[\s\S]*?</textarea>", "", html, flags=re.I)

    block = extract_article_block(html)
    if block is not None:
        html = block
    else:
        m = re.search(r"<body[^>]*>([\s\S]*?)</body>", html, flags=re.I)
        if m:
            html = m.group(1)
        html = re.sub(r"</?(html|head|body)[^>]*>", "", html, flags=re.I)
        # 兜底：剥掉页面外壳里的提示语 / 复制条（老模板没有 id="article" 时）
        html = SHELL_CLASS_RE.sub("", html)

    html = strip_textnode_whitespace(html)  # 消除段前空格（微信把换行缩进渲染成空白）
    return html.strip()


def process_images(token, html, base_dir):
    def repl(m):
        tag = m.group(0)
        src_m = re.search(r'src\s*=\s*["\']([^"\']+)["\']', tag)
        if not src_m:
            return tag
        src = src_m.group(1).strip()
        if src.startswith("http") and "mmbiz.qpic.cn" in src:
            return tag
        local = None
        if not src.startswith(("http://", "https://", "data:")):
            cand = src if os.path.isabs(src) else os.path.join(base_dir, src)
            if os.path.exists(cand):
                local = cand
        elif src.startswith("http"):
            try:
                tmp = os.path.join(BASE_DIR, "_tmp_dl_" + uuid.uuid4().hex[:8] +
                                   (".png" if ".png" in src.lower() else ".jpg"))
                req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as wf:
                    wf.write(r.read())
                local = tmp
            except Exception as e:
                print("[警告] 外链图下载失败，保留原样：", src, str(e)[:50])
        if not local:
            return tag
        try:
            wx_url = upload_content_image(token, local)
            print("  [图] 已上传正文图片:", os.path.basename(local))
            return tag.replace(src_m.group(1), wx_url)
        except Exception as e:
            print("  [警告]", str(e))
            return tag
        finally:
            if local.startswith(os.path.join(BASE_DIR, "_tmp_dl_")) and os.path.exists(local):
                os.remove(local)
    return re.sub(r"<img[^>]*>", repl, html, flags=re.I)


def make_default_cover(title):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None  # 未装 Pillow：调用方需用 --cover 指定封面
    img = Image.new("RGB", (900, 383), (200, 16, 46))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 900, 6], fill=(248, 195, 0))
    d.rectangle([0, 377, 900, 383], fill=(248, 195, 0))
    cx = 450
    d.ellipse([cx - 6, 178, cx + 6, 190], fill=(248, 195, 0))
    d.ellipse([cx - 6, 198, cx + 6, 210], fill=(248, 195, 0))
    font = None
    for fp in FONT_CANDIDATES:
        if os.path.exists(fp):
            try:
                font_big = ImageFont.truetype(fp, 48)
                font = font_big
                break
            except Exception:
                pass
    if font:
        text = title[:14] + ("…" if len(title) > 14 else "")
        w = d.textlength(text, font=font)
        d.text(((900 - w) / 2, 160), text, fill=(255, 255, 255), font=font)
    path = os.path.join(BASE_DIR, "_auto_cover.png")
    img.save(path, "PNG")
    return path


def extract_first_image(html, base_dir):
    m = re.search(r'<img[^>]*src\s*=\s*["\']([^"\']+)["\']', html)
    if not m:
        return None
    src = m.group(1)
    if src.startswith(("http://", "https://")):
        return None
    cand = src if os.path.isabs(src) else os.path.join(base_dir, src)
    return cand if os.path.exists(cand) else None


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(description="推送 HTML 文章到微信公众号草稿箱")
    ap.add_argument("--account", default="", help="公众号名（accounts.json 的键）")
    ap.add_argument("--accounts-file", default="", help="skill 目录外的私有配置文件路径")
    ap.add_argument("--html", required=True, help="文章HTML文件路径")
    ap.add_argument("--title", required=True, help="文章标题")
    ap.add_argument("--original-title", default="", help="原文标题（写入摘要，当未指定--digest时使用）")
    ap.add_argument("--digest", default="", help="摘要（不填优先原文标题，其次正文前54字）")
    ap.add_argument("--cover", default="", help="封面图路径（不填自动处理）")
    ap.add_argument("--author", default="", help="作者名（不填则读同目录 article_info.json 的 author，再没有用账号名）")
    ap.add_argument("--source-url", default="", help="原文链接（写入公众号「原文链接」字段）")
    ap.add_argument("--media-id", default="", help="已存在草稿的media_id；传入则更新而非新建")
    args = ap.parse_args()

    accounts_path = args.accounts_file or DEFAULT_ACCOUNTS_PATH

    account = args.account
    if not account:
        accounts = load_accounts(accounts_path)
        if len(accounts) == 1:
            account = next(iter(accounts))
            print("[提示] 未指定 --account，自动使用配置中唯一的公众号「%s」" % account)
        else:
            print("[错误] 请用 --account 指定公众号。")
            names = "、".join(accounts.keys()) if accounts else "(空，见下方配置引导)"
            print("[提示] 配置文件 %s 中的账号：%s" % (accounts_path, names))
            sys.exit(1)

    cfg = resolve_credentials(account, accounts_path)

    html_path = os.path.abspath(args.html)
    base_dir = os.path.dirname(html_path)

    # 兜底读取抓取产出元信息（作者署名 / 原文链接），未显式传参时自动使用
    info = {}
    info_path = os.path.join(base_dir, "article_info.json")
    if os.path.exists(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            info = {}
    author = args.author or info.get("author", "") or cfg.get("author", account)
    source_url = args.source_url or info.get("source_url", "")
    author = author[:8]  # 微信 author 字段限 8 字（超长报 45110）

    print("=" * 52)
    print("[%s] 推送到草稿箱  %s" % (account, time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 52)

    token = get_token(cfg)
    print("[1] access_token 就绪")

    content = load_article_html(html_path)
    # 封面前置：在正文图片被替换成远程URL之前，先锁定正文第一张本地图
    first_local_img = extract_first_image(content, base_dir)
    content = process_images(token, content, base_dir)
    print("[2] 正文处理完成（内联样式排版已保留），长度 %d 字符" % len(content))

    cover = args.cover
    if cover and os.path.exists(cover):
        cover_path = cover
    else:
        cover_path = first_local_img or make_default_cover(args.title)
    if not cover_path or not os.path.exists(cover_path):
        print("[错误] 找不到可用封面，请用 --cover 指定")
        sys.exit(1)
    thumb_media_id = upload_cover_material(token, cover_path)
    print("[3] 封面上传成功:", os.path.basename(cover_path))

    if not args.digest:
        if args.original_title:
            args.digest = args.original_title[:120]
        else:
            plain = re.sub(r"<[^>]+>", "", content)
            plain = re.sub(r"\s+", "", plain)
            args.digest = plain[:54]

    print("    作者: %s   原文链接: %s" % (author, source_url or "(无)"))

    article = {
        "title": args.title,
        "author": author,
        "digest": args.digest,
        "content": content,
        "content_source_url": source_url,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }
    if args.media_id:
        url = "https://api.weixin.qq.com/cgi-bin/draft/update?access_token=" + token
        payload = json.dumps({"media_id": args.media_id, "index": 0,
                              "articles": article}, ensure_ascii=False).encode("utf-8")
        d = http_json(url, data=payload, headers={"Content-Type": "application/json; charset=utf-8"})
        if d.get("errcode", 0) == 0:
            print("[4] 草稿更新成功！media_id =", args.media_id)
            print(">>> 请到 mp.weixin.qq.com → 草稿箱 查看《%s》" % args.title)
        else:
            print("[4] 草稿更新失败：", json.dumps(d, ensure_ascii=False))
            if d.get("errcode") == 40164:
                print(">>> IP白名单问题，按报错提示把新IP加进白名单后重试即可")
            sys.exit(1)
        return
    url = "https://api.weixin.qq.com/cgi-bin/draft/add?access_token=" + token
    payload = json.dumps({"articles": [article]}, ensure_ascii=False).encode("utf-8")
    d = http_json(url, data=payload, headers={"Content-Type": "application/json; charset=utf-8"})
    if "media_id" in d:
        print("[4] 草稿创建成功！media_id =", d["media_id"])
        print(">>> 请到 mp.weixin.qq.com → 草稿箱 查看《%s》" % args.title)
    else:
        print("[4] 草稿创建失败：", json.dumps(d, ensure_ascii=False))
        if d.get("errcode") == 40164:
            print(">>> IP白名单问题，按报错提示把新IP加进白名单后重试即可")
        sys.exit(1)


if __name__ == "__main__":
    main()
