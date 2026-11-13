# 桌面图标台球 · Desktop Icon Billiards

**把 Windows 桌面图标摆成一局台球。** 在桌面上移动鼠标瞄准，按住左键蓄力，松手击球。图标保留自己的缩略图，会滚动、碰撞和入袋。退出后，桌面恢复原样。

Windows 10 / 11 x64 · 单文件 EXE · MIT

![实机画面：桌面图标排成球阵，白球瞄准库边，虚线显示反弹路线](docs/images/desktop-gameplay.jpg)

## 下载

Windows 版 v0.1.6 为单文件 EXE。[GitHub 下载](https://github.com/liuxiaogang-com/desktop-icon-billiards/releases/download/v0.1.6/DesktopIconBilliards-0.1.6-win-x64.exe) · [CNB 国内镜像](https://cnb.cool/liuxiaogang/desktop-icon-billiards/-/releases/download/v0.1.6/DesktopIconBilliards-0.1.6-win-x64.exe)

`desktop-icon-billiards` 的源码和发布附件在两个平台同步。[GitHub 仓库](https://github.com/liuxiaogang-com/desktop-icon-billiards) · [CNB 国内镜像仓库](https://cnb.cool/liuxiaogang/desktop-icon-billiards)

官网入口为 [桌面图标台球](https://go.liuxiaogang.com/zmtq)。

## 玩法

1. 启动后等桌面图标摆好。移动鼠标选择白球的出球方向，**按住左键蓄力，松手击球**。按下的位置旁会显示「力度」进度条。
2. 用「**辅助线 · 开 / 关**」切换瞄准辅助。白线标记首次碰撞前的路线，蓝线表示白球碰球或碰库后的方向，绿线表示被撞图标的方向。路线会随力度变化，终点是预计的下一次碰撞、入袋或停球位置。辅助线带有描边，并会根据壁纸明暗自动换成深色或浅色，浅色壁纸上也看得清。路线上还会淡淡地画出碰撞圈：白球接触瞬间的位置，以及被撞图标的碰撞范围（比图标本身略大，这就是实际判定碰撞的圆）；图标被撞到时也会闪现一圈。
3. 主窗口显示本局用时、进球数和剩余图标数。白球入袋时会尝试自动摆回，如果没有合适的位置，可以点击台面空处摆球。白球入袋不计入进球数。
4. 清台后查看总用时和图标总数。点击「**重新开始**」重置本局，也可以在中途重开。
5. 点击「退出」、按 `Esc` 或使用全局快捷键 `Ctrl+Alt+X`，恢复桌面。

### 计时与全球排名

- 用时从**第一杆击出**算起，到最后一颗图标入袋为止，精确到毫秒。开球前的瞄准不计时。清台后会显示「本局平均 X 秒/颗」，也就是总用时除以图标总数。
- 球数固定在 **15～45 颗**。桌面图标少于 15 个时，复制现有图标补足到 15 颗；多于 45 个时只取其中 45 个，优先快捷方式和程序，其次是文件，文件夹排在最后，同类按桌面顺序取。主窗口会显示「桌面 N 个图标，复制补到 / 取其中 X 颗」。补球和落选的图标都只在游戏画面里生效，不会改动桌面，退出后照常恢复。
- 「全球排名」**默认关闭**。第一次启动会弹出一次询问，选「是」才开启，选「否」不会上传任何东西；之后可随时用主窗口的「排名」按钮开关。开启后，每局清台时提交一次成绩，用平均每颗用时排名。
- 开启排名需要联网，没有网络或服务不可用时游戏不受影响，只是这一局不计入排名。
- 每局清台后会弹出**成绩卡片**：游戏名与图标、段位（传奇球王、桌面球王、大师、高手、熟手、新秀）、全球名次和超过的玩家比例、用时、平均每颗用时、图标数、击球数和个人最佳，底部带有作者标记。点「保存图片」会存成 1080×1350 的 PNG（在「图片」文件夹的 `DesktopIconBilliards` 里），点「复制图片」可直接粘贴到微信、QQ 或帖子里。点击「查看排行榜」可查看全球前 50 条个人最佳成绩，自己的名次高亮并固定显示在底部。卡片只含成绩，不会出现你的桌面图标和文件名；没开启排名时卡片以平均每颗用时为主，并提示开启排名。

![成绩卡片示例：大师段位，全球第 1,204 名，超过 87% 的玩家](docs/images/result-card.png)

### 窗口与隐藏

- 主窗口只保留**无标题栏、无状态栏的横条**：用时、进球和剩余数量与全部操作按钮排成一行，无折叠菜单。拖动左侧手柄或数据区域移动横条，位置会自动保存；点击「退出」恢复桌面。
- **0.1.4 暂时停用隐藏功能**：不显示隐藏按钮，`Ctrl+Alt+H` 不注册。需要离开时请点击「退出」或按 `Ctrl+Alt+X` 恢复桌面。
- **台面**开关可隐藏台面框、球袋和壁纸衬底，只剩图标在桌面上滚动。
- 图标入袋时会滑进球袋、旋转缩小并淡出，同时泛起一圈涟漪。
- 作者标记 `GitHub@liuxiaogang-com` 显示在横条右侧（点击可打开作者主页）、结算窗口右上角，以及台面下方的边缘处（很淡，不影响击球）。转发截图或录屏时请保留来源。
- 遇到无法击球、多显示器错位等问题，请把 `%TEMP%\DesktopIconBilliards` 下的 `diag.log` 和 `error.log` 一并反馈。

![清台结算窗口：总用时 06:12，图标总数 35](docs/images/round-summary.jpg)

## 局域网对战

两台电脑各开一个游戏，在同一张台面上轮流打，比谁进球多。

1. 双方都打开游戏，等桌面图标摆好。
2. 主机点横条上的「**联机**」→「创建房间」，记下 4 位**房间码**和本机局域网 IP。
3. 客机点「联机」→「加入房间」，可用「扫描局域网」自动列出房间，也可手填 IP 与房间码，然后「加入」。
4. 连上后台面换成对战的固定球阵（15 颗），横条显示「我 0 : 0 对手」。**轮到谁，谁才能瞄准击球**，另一端的球杆隐藏。
5. 进球继续打，空杆换手；白球入袋算犯规，由对手把白球摆回再击球。清台后按进球数判胜负。
6. 任一方断开，另一方收到提示并回到单人模式。

对战说明：

- 台面是固定的 **16:9 逻辑台面 + 15 颗球**，两端各自等比缩放到自己的屏幕上，所以两台机器分辨率不同也能打，几何与判定完全一致；桌面摆的仍是**各自**的图标。
- 只有主机计算物理，客机把击球意图发给主机，主机按 20Hz 广播状态回来。
- 默认走 TCP **45872**，发现用 UDP 广播 **45871**。Windows 第一次监听可能弹防火墙授权，需要允许，否则对方连不上（本机回环不受影响）。
- 房间码只防误连、不加密，同一局域网内可见。
- 联机对战**不计入**全球排名。
- 协议与设计细节见 [docs/NETPLAY.md](docs/NETPLAY.md)。

## 从源码运行

需要 Windows 10 / 11 x64、Explorer 桌面和 Python 3.11+（含 Tk）。下面用 [uv](https://docs.astral.sh/uv/) 安装 Python 3.12 和运行依赖。

```powershell
uv venv --managed-python --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
.\run.cmd
```

环境准备好后，也可以双击 `run.cmd`。如果双击后没有窗口，从 PowerShell 运行 `.\.venv\Scripts\python.exe -X utf8 app.pyw` 查看错误。若访问 Explorer 时遇到「拒绝访问」，请在当前登录用户的普通桌面会话运行。使用 Microsoft Store 版 Python 创建过虚拟环境时，可移走旧的 `.venv` 后按上面的命令重建。

## 构建与测试

打包需要 PowerShell 7 和 uv。

```powershell
pwsh -File build.ps1
```

产物位于 `dist/DesktopIconBilliards-<version>-win-x64.exe`，可以直接运行。脚本会输出大小和 SHA-256，版本号取自 `dib/__init__.py`。已安装构建依赖时，可加 `-SkipDeps`。

脚本会将发布 EXE 的 Windows 完整性标签设为普通用户的 Medium，避免继承工作目录的 Low 标签后无法创建解压临时目录（`Could not create temporary directory!`）。这一步只修改产物标签，不修改项目目录或系统临时目录的权限。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

- [更新源格式和发布步骤](docs/UPDATE.md)
- [示例更新清单](update.example.json)
- 图标原图位于 `assets/desktop-icon-billiards.png`。修改后运行 `.\.venv\Scripts\python.exe tools\make_icon.py`，生成七种尺寸的 Windows ICO。

## 桌面与隐私

游戏通过 Windows Shell 读取桌面图标的缩略图和布局，用独立的桌面图层绘制游戏画面。台面内的高斯模糊来自本地壁纸文件。游戏不截取桌面画面，也不采集音频。

只有主动开启「全球排名」后，游戏才会联网上传：每局的用时、图标数量、击球次数，以及一个匿名设备标识（由本机系统 ID 加盐哈希得到，无法还原）。不会上传图标名称、桌面截图或文件信息。设备凭据保存在 `%APPDATA%\DesktopIconBilliards\rank.json`。排名服务的地址由 `dib/config.py` 中的 `DEFAULT_RANK_URL` 指定，开发时可用环境变量 `DIB_RANK_URL` 覆盖；排名服务端在 [desktop-icon-billiards-server](https://cnb.cool/liuxiaogang/desktop-icon-billiards-server)。

退出时会恢复桌面，独立看门狗也会在游戏进程异常退出后尝试恢复。运行记录位于 `%TEMP%\DesktopIconBilliards`。游戏期间若更改分辨率、DPI 或屏幕布局，本局会停止并恢复桌面。桌面图标需处于显示状态，同一时间只运行一个实例。

## 打赏

如果你喜欢这个项目，可以用微信扫码赞赏。感谢支持！

<img src="assets/donation.jpg" alt="微信赞赏码" width="240">

[打开赞赏图片](assets/donation.jpg)

## 许可证

[MIT](LICENSE) · Copyright (c) 2025–2026 LXG
