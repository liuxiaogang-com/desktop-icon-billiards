# 桌面图标台球 v0.2.0 · 新增隐藏台面模式

## 隐藏台面模式
- 关闭台面后，六个洞口显示为电脑原生回收站图标，尺寸与桌面图标一致；开启台面时仍使用圆形洞口。（[#6](https://github.com/liuxiaogang-com/desktop-icon-billiards/issues/6)、[#10](https://github.com/liuxiaogang-com/desktop-icon-billiards/issues/10)）
- 工具条自动收起，原位置保留小眼睛，鼠标移上去即可恢复，无需点击。
- 按住 Shift 临时显示半透明球杆和辅助线，松开隐藏；蓄力时显示半透明力度条。
- 每次进入模式显示简短操作提示，Ctrl+Alt+X 可退出并恢复桌面。

## 击球体验
- 修正薄切碰撞方向，改善辅助线与实际出球方向的一致性。
- 修正四角洞口入口判定，改善“看着能进、实际进不去”的问题。（[#2](https://github.com/liuxiaogang-com/desktop-icon-billiards/issues/2)）

## 启动兼容性
- 修复桌面接口暂未就绪时，因返回空对象而导致启动失败的问题。（[#4](https://github.com/liuxiaogang-com/desktop-icon-billiards/issues/4)）
- 修正部分环境下桌面图标窗口选择错误的问题，补充“拒绝访问”的诊断信息。（[#5](https://github.com/liuxiaogang-com/desktop-icon-billiards/issues/5)、[#8](https://github.com/liuxiaogang-com/desktop-icon-billiards/issues/8)）
- 权限拒绝及混合缩放双屏环境仍需反馈用户复测。

## 其他
- README 和赞赏窗口补充说明：赞赏是对这个项目的自愿支持，不包含额外功能或专属技术支持。

Windows 10 / 11 x64 · 单文件 EXE。退出旧版后运行新版即可。
