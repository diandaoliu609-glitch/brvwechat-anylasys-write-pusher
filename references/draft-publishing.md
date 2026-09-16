# 草稿推送

随附 `scripts/push_draft.py` 和 `scripts/push_draft_multi.py` 来自已审阅的多账号 API 推送实现。它们只调用微信官方 `api.weixin.qq.com` 接口，支持内联 HTML、正文图片上传、封面上传、单篇/多图文草稿和草稿更新。

## 凭据与配置

把 `templates/accounts.example.json` 复制为 skill 目录外、权限为仅当前用户可读的私有文件，例如 `~/Library/Application Support/brvwechat/accounts.json`。每个账号使用一个键名；调用时仅传键名，不在命令行、文章或日志中写 AppSecret。

```json
{
  "账号显示名": {
    "appid": "wx...",
    "appsecret": "...",
    "author": "署名"
  }
}
```

## 单篇草稿

在用户明确要求推送、已完成内容审校、封面与正文图片均已确认后执行：

```bash
python3 scripts/push_draft.py \
  --account "账号显示名" \
  --accounts-file "/absolute/path/accounts.json" \
  --html "/absolute/path/article.html" \
  --title "短标题" \
  --digest "摘要" \
  --cover "/absolute/path/cover.png"
```

正文只应包含可推送内容。若 HTML 是本地预览页，请用 `<div id="article">…</div>` 包住正文，避免把复制按钮和提示语写进公众号。脚本会移除 script、style、button 和 textarea；推送前仍需检查文章 HTML。

## 多图文与更新

多图文最多 8 篇，数组第一个元素是头条：

```bash
python3 scripts/push_draft_multi.py \
  --account "账号显示名" \
  --accounts-file "/absolute/path/accounts.json" \
  --config "/absolute/path/items.json"
```

脚本返回的 `media_id` 是草稿创建结果。要更新已有草稿，必须先通过微信草稿列表接口核对真实 `media_id`；若微信返回不支持更新，不要擅自删除旧草稿。

## 常见问题与停止条件

- `40164`：出口 IP 不在白名单。记录报错提示的 IP，由账号管理员在公众号后台添加后再试。
- `45110`：作者字段过长；保持不超过 8 个字符。
- 推送失败、封面或图片上传失败时停止，不重复盲推。
- 草稿创建成功后由人工到 `mp.weixin.qq.com` 预览、核对链接和排版，再决定是否群发。
