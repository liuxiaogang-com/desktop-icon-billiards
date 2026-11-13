# 更新接口与发布流程

桌面图标台球每次启动会自动检查一次更新，窗口里也提供「检查更新」按钮。更新源是一个
HTTPS JSON 文件，客户端读取后只做提示：点击「去更新」会用默认浏览器打开下载地址，
**不会自动下载、替换或运行 exe**。发布新版本时按本文档更新这个 JSON 即可。

检查在后台线程进行，不会阻塞游戏。自动检查失败（无网络、超时、JSON 非法、非 HTTPS 等）
会静默跳过并写入运行日志；手动「检查更新」失败时会弹窗提示“检查失败，请稍后重试”，
两种情况都不影响游戏启动和运行。

默认地址是编译进 exe 的 `dib/config.py` 常量 `DEFAULT_UPDATE_URL`，修改它需要重新打包；
仅临时验证预发布源时用 `DIB_UPDATE_URL` 覆盖即可，无需重新构建。发布前必须确认默认地址
返回的是本文档约定的格式。

## 更新源地址

- 客户端默认请求 `https://go.liuxiaogang.com/desktop-icon-billiards-update`。
- 请求查询参数包含 `app=DesktopIconBilliards`、`version=<当前安装版本>` 和 `source`。启动自动检查用 `source=startup`，手动点击检查更新用 `source=manual`；可在短链入口统计请求来源。客户端保留地址已有的其他查询参数。
- 短链用 HTTP 302 指向 [CNB 主分支的 update.json](https://cnb.cool/liuxiaogang/desktop-icon-billiards/-/git/raw/main/update.json)，由该文件返回 UTF-8 JSON。每次发版更新 CNB 上的文件即可。
- 预发布/联调可临时覆盖：设置环境变量 `DIB_UPDATE_URL`，进程启动时读取，exe 无需重新打包。

```powershell
# 仅对当前 PowerShell 会话生效，用于验证预发布 JSON
$env:DIB_UPDATE_URL = 'https://staging.example.com/desktop-icon-billiards/update.json'
.\dist\DesktopIconBilliards-0.1.0-win-x64.exe
```

`DIB_UPDATE_URL` 同样只接受 `https://` 地址；验证完记得关闭该会话，避免正式运行时仍指向预发布源。

## JSON 格式

顶层固定三个必填字段，`history` 为可选字段。完整示例见仓库根目录的 `update.example.json`。

```json
{
  "version": "0.1.1",
  "download_url": "https://example.com/DesktopIconBilliards-0.1.1-win-x64.exe",
  "notes": "本次更新内容，换行用 \n。",
  "history": [
    {"version": "0.1.0", "notes": "首个公开版本。"}
  ]
}
```

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `version` | 是 | 字符串 | 最新版本号，必须是 `major.minor.patch` 三段非负整数，如 `0.1.1`。 |
| `download_url` | 是 | 字符串 | 最新版 exe 的 HTTPS 下载地址，客户端只用这一个地址。 |
| `notes` | 是 | 字符串 | 最新版更新说明，最多 4000 字符，换行写 `\n`。 |
| `history` | 否 | 数组 | 旧版本说明，仅用于界面展示；每项包含 `version` 和 `notes` 两个字符串。 |

约定：

- 顶层 `version` 永远是最新版，下载地址只取顶层 `download_url`；不要把最新版放进 `history`。
- `history` 只放旧版本，别放比顶层更新的版本；建议按版本从新到旧排列，最多保留最近约 20 条。
- 版本比较按数字进行（`0.10.0` 比 `0.9.0` 新），不是字符串排序。
- 说明文字按纯文本显示，不支持 HTML/Markdown。
- 顶层结构不要改成数组，字段名和类型也不要改，否则旧客户端无法识别。

## 硬性限制

1. **只接受 HTTPS**：更新源地址和 `download_url` 都必须是 `https://`，HTTP 会被拒绝。
2. **整个文件不超过 65536 字节（64 KiB）**：按 UTF-8 编码后的字节数计算。中文一个字通常占 3 字节，
   所以不能只按字符数估算；内容太多时删减 `history` 或缩短 `notes`，并建议留出一定余量。
3. **`notes` ≤ 4000 字符**：顶层说明超长时客户端会拒绝该更新源；`history` 中单条说明同样限 4000 字符，
   超长的历史条目会被忽略。
4. **版本号必须合法**：`version` 必须是 `major.minor.patch` 三段非负整数（每段最多 6 位）。
5. **下载地址必须是完整 HTTPS URL**：必须带主机名，且不能包含用户名或密码。

## 发布清单

1. 修改 `dib/__init__.py` 的 `__version__`，这是全仓库唯一的版本来源。
   spec 文件、`version_info.txt`、构建脚本都不写死版本号，不要手改它们。
2. 在干净的 Windows 环境构建：

   ```powershell
   pwsh -File build.ps1
   ```

   产物为 `dist/DesktopIconBilliards-<version>-win-x64.exe`，脚本会打印大小和 SHA-256。
   旧版本产物不会被删除，方便回滚。
3. 先把 exe 上传为 GitHub Release 附件；CNB 附件通过发布分支流水线上传，不使用 CLI 直接创建 Release。
   发布分支的 `.cnb.yml` 从 GitHub 下载同一份 exe，用 `SHA256SUMS.txt` 校验，再通过 `git:release`
   创建版本、`cnbcool/attachments` 上传附件（`ttl: 0` 永久保存）。更新说明从 `RELEASE.md` 读取。
   可参考 CNB 的 `codex/publish-desktop-icon-billiards-v0.1.4` 分支；后续发布需同步修改版本号和校验值。
   流水线成功后，确认两边都能通过 HTTPS 匿名下载，且 SHA-256 与本地产物一致。
   文件名保持 `DesktopIconBilliards-<version>-win-x64.exe` 格式。
4. 更新 JSON：把上一版顶层的 `version` 和 `notes` 移到 `history` 最前面，再写入新的
   `version`、`download_url`、`notes`。`download_url` 填本次 CNB 镜像的下载链接。
5. 校验 JSON：能正常解析、版本号合法、整个文件不超过 65536 字节。
6. 用旧版本客户端验证：启动后能看到新版本号与说明，「去更新」能打开正确的下载地址，
   取消后照常游戏。预发布源用 `DIB_UPDATE_URL` 验证，不要为此改代码。
7. 上线后保留历史说明，不要清空 `history`。

## 本地校验

```powershell
# 必须是合法 JSON
$feed = Get-Content -Raw -LiteralPath update.example.json | ConvertFrom-Json
$feed.version
$feed.download_url

# UTF-8 字节数必须不超过 65536
(Get-Item -LiteralPath update.example.json).Length

# 顶层说明不超过 4000 字符
$feed.notes.Length
```

## 兼容性说明

- 旧客户端只读顶层 `version`、`download_url`、`notes`，会忽略 `history`，所以新增历史条目
  不会影响它们发现和下载最新版。
- 新客户端在没有 `history` 的旧格式 JSON 上也能正常工作。
- 64 KiB 上限来自已发布客户端。不要提高客户端读取上限来迁就超长 JSON，应删减内容。
- 自动检查失败（无网络、超时、JSON 非法、非 HTTPS 等）只会跳过提示并写入日志，
  手动检查失败会提示“检查失败，请稍后重试”，都不影响游戏启动。
