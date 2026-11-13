# 局域网对打（LAN versus）设计契约

本文是 `feat/lan-versus` 分支的实现契约。四个工作包各自拥有互不重叠的文件，
所有跨模块调用只允许使用本文写明的接口。

| 工作包 | 独占文件 |
|---|---|
| 传输层 `netplay` | `dib/netplay.py`、`tests/test_netplay.py` |
| 规则层 `table` | `dib/table.py`、`tests/test_table_versus.py` |
| 渲染层 `overlay` | `dib/overlay.py` |
| 界面层 `ui` | `dib/ui.py`、`dib/panel.py`、`dib/versus.py`、`tests/test_versus_ui.py` |

## 1. 为什么不是确定性锁步

`BilliardsMotion.step(dt, now)` 的 `dt` 来自帧时钟（`runner.py` 里 `min(0.05, now - last)`），
内部再按速度自适应子步（`steps = ceil(dt * max(180, speed / ...))`）。
两台机器帧率、卡顿不同，积分的子步数就不同，轨迹必然分叉。
因此采用**主机权威 + 快照同步**：只有主机跑物理，客机只负责渲染与采集输入。

## 2. 逻辑台面（唯一能让两台机器看到同一张桌子的办法）

单人模式的台面坐标 = 桌面父窗口像素坐标，布局从本机 `work_area` 推导，两台机器天然不同。
对战模式改用**固定逻辑坐标系**：

```
LOGICAL_BOUNDS = (0.0, 0.0, 1600.0, 900.0)   # 16:9，与屏幕无关
VERSUS_BALLS   = 15
```

`BilliardsMotion.__init__` 内部对 `work_area` 只做一次固定比例的收边
（左 6%、上 9%、右 6%、下 9%），球数固定后 `pitch / tile / radius / white_radius /
pocket_radius / rack` 全部由 `(bounds, count)` 唯一确定。因此两台机器各自构造

```python
BilliardsMotion(LOGICAL_BOUNDS, local_icon_sizes, count=VERSUS_BALLS)
```

就得到**逐位相同**的球阵、库边与袋口。`local_icon_sizes` 只影响 `_poses()`
里的贴图长宽比，不参与物理。主机传参时必须显式 `count=15`，客机同。

渲染层负责把逻辑坐标映射到本机屏幕上的 16:9 信箱区（letterbox），
**等比缩放**（不拉伸，球必须还是圆的），因此两台机器只是台面大小不同，几何与判定完全一致。

## 3. 传输层接口（`dib/netplay.py`）

只依赖标准库：`socket`、`struct`、`threading`、`json`、`time`、`secrets`、`queue`。
**不得**引入新第三方依赖，**不得**自己实现物理或触碰 `table`。

### 常量

```python
PROTOCOL_VERSION = 1
DISCOVERY_PORT   = 45871
GAME_PORT        = 45872
LOGICAL_BOUNDS   = (0.0, 0.0, 1600.0, 900.0)
VERSUS_BALLS     = 15
STATE_HZ         = 20.0     # 主机快照频率
HEARTBEAT_SECONDS= 1.0
TIMEOUT_SECONDS  = 5.0      # 超过即判定断线
MAX_FRAME_BYTES  = 65536
```

### 帧格式

TCP 上采用 **4 字节大端长度 + UTF-8 JSON 对象**，长度 1..65536，超限即断开。
每条消息是 dict，必须含 `"t"`（类型字符串）与 `"v"`（协议版本）。

### 异常与类

```python
class NetError(Exception):
    """握手失败、版本不符、房间码错误、超时。消息可直接显示给玩家（中文）。"""

class Session:
    role: str            # 'host' | 'guest'
    code: str            # 房间码，主机生成，客机填入
    port: int            # 实际监听端口（port=0 时由系统分配，测试依赖此项）
    status: str          # 'waiting'（仅主机，尚未接入）| 'connected' | 'closed' | 'error'
    error: Exception|None
    peer_name: str       # 对端机器名
    def send(self, message: dict) -> None:
        """线程安全。未连接时进入待发队列（上限 32 条，超出丢最旧）。"""
    def drain(self) -> list[dict]:
        """非阻塞取走已收到的全部消息，按到达顺序。动画线程每帧调用。"""
    def close(self, reason: str = '') -> None:
        """幂等。关闭套接字、结束线程，并尽力发送一条 {"t":"bye"}。"""

def start_host(code: str, port: int = GAME_PORT, discovery: bool = True) -> Session:
    """立即返回。后台线程 accept 一个客机；握手成功后 status 变 'connected'。"""

def join(host: str, port: int = GAME_PORT, code: str = '', timeout: float = 6.0) -> Session:
    """阻塞直到握手完成或失败（抛 NetError）。失败必须清理套接字。"""

def discover(timeout: float = 1.5, address: str = '255.255.255.255',
             port: int = DISCOVERY_PORT) -> list[dict]:
    """UDP 广播找房间，返回 [{'host','port','room','name','version'}]，按到达顺序去重。"""

def local_addresses() -> list[str]:
    """本机可被局域网访问的 IPv4 列表，用于界面展示。排除回环。"""

def make_code() -> str:
    """4 位房间码，字符集 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'（去掉 I/O/0/1）。"""
```

### 消息

| 方向 | 消息 | 说明 |
|---|---|---|
| 客→主 | `{"t":"hello","v":1,"code":"ABCD","name":"PC-B"}` | 首帧必须 |
| 主→客 | `{"t":"welcome","v":1,"name":"PC-A"}` | 房间码正确 |
| 主→客 | `{"t":"reject","v":1,"why":"房间码不对"}` | 随后主机断开 |
| 主→客 | `{"t":"setup","v":1,"balls":15,"bounds":[0,0,1600,900],"state":{...}}` | 首个快照，见 §5 |
| 客→主 | `{"t":"shot","v":1,"seq":7,"aim":[-1,-0.02],"power":0.73}` | 客机击球意图 |
| 客→主 | `{"t":"place","v":1,"seq":8,"x":300.0,"y":400.0}` | 客机摆白球 |
| 客→主 | `{"t":"restart","v":1}` | 请求重开 |
| 主→客 | `{"t":"nack","v":1,"seq":7,"why":"还没轮到你"}` | 拒绝某条意图 |
| 主→客 | `{"t":"state","v":1,"state":{...},"ms":12345}` | 快照，20Hz + 关键事件立即发 |
| 双方 | `{"t":"bye","v":1,"why":"..."}` | 主动断开 |
| 双方 | `{"t":"ping","v":1}` / `{"t":"pong","v":1}` | 心跳，1 秒一次 |

`state` 结构由 `table.snapshot()` 定义（§5），传输层**不解析**它，原样搬运。

### 线程规则

- 读线程只做「收帧 → 解析 → 入 inbox」，**绝不**碰 `table`、Tk 或任何绘图对象。
- 写操作由 `send()` 加锁后直接写套接字（非阻塞失败则入待发队列重试）；
  写失败不抛异常，置 `status='error'`。
- `drain()` 与 `send()` 可被不同线程同时调用。

## 4. 规则层接口（`dib/table.py`）

保持单人模式行为**逐位不变**：`BilliardsMotion(work_area, sizes)` 的默认路径不得改动。

### 构造

```python
BilliardsMotion(work_area, sizes, count=None)
```
- `count is None`：沿用现有按图标数量取 15..45 颗的行为。
- `count` 为整数：强制球数为该值，多余 `sizes` 不参与布局。

### 对战状态

```python
self.versus: bool          # 是否处于对战模式
self.turn: int             # 0 = 主机, 1 = 客机
self.scores: list[int]     # [主机进球, 客机进球]
self.outcome: int          # 0 未结束, 1 主机胜, 2 客机胜, 3 平局
self.authoritative: bool   # 单人/主机 True，客机 False
self.local_side: int       # 本机是哪一边
```

### 方法

```python
def start_versus(self, count=VERSUS_BALLS, first=0) -> None
    """重置为对战局：固定球数、清比分、turn=first、outcome=0、versus=True。"""

def snapshot(self) -> dict
    """返回可 JSON 序列化的快照，键见 §5。"""

def apply_snapshot(self, state: dict) -> None
    """客机把主机快照写进本机状态（位置、角度、入袋、exits、state、turn、比分、outcome）。"""

def can_shoot(self) -> bool
    """本机现在是否有权击球：state=='ready' 且 turn == local_side。"""

def shoot(self, power, now=None) -> bool
    """客机上不再改物理：返回 False 并记录 self.pending_shot = (aim, power)。
    主机/单人保持原有行为。"""

def place_white(self, x, y) -> bool
    """客机同理记录 self.pending_place = (x, y) 并返回 False。"""
```

### 回合计分规则（必须完全按此实现）

击球前记 `shooter = turn`。一杆停下后（原 `step()` 里判定停球的那一处）：

1. `scores[shooter] += 本杆入袋数`
2. 清台 → `state='finished'`、`outcome = 1/2/3`（比较比分）
3. 白球入袋 → `state='return_white'` → `'ball_in_hand'`，`turn = 1 - shooter`（犯规，换对手摆球）
4. 本杆有进球 → `turn` 不变（继续打）
5. 本杆没进球 → `turn = 1 - shooter`

`pocketed_icons` 等单人语义保持不变。对战模式下 `play_ms` 由主机快照里的 `ms` 覆盖
（新增 `self.remote_ms`，为 `None` 时走原逻辑）。

## 5. 快照结构（`table.snapshot()` / `apply_snapshot()`）

```python
{
  "serial": int,          # round_serial，用来识别重开
  "state": str,           # ready/stroke/rolling/return_white/ball_in_hand/finished
  "white": [x, y],        # 逻辑坐标
  "white_sunk": 0/1,
  "balls": [[x, y, angle, impact], ...],   # 长度 == 球数
  "sunk": [0/1, ...],     # 长度 == 球数，下标对应 bodies（含白球在 0）
  "exits": {"3": [x, y, tx, ty, age]},     # 键是字符串（JSON）
  "turn": 0/1,
  "scores": [a, b],
  "shots": int,
  "ms": int,              # play_ms，客机显示用时用
  "outcome": 0/1/2/3,
  "charge": float,        # 仅让客机画力度条，主机自己也在用
}
```

浮点保留 2 位小数（`round(v, 2)`）以压小包体，客机渲染无需更高精度。

## 6. 渲染层接口（`dib/overlay.py`）

新增，单人模式全部走原路径（`self.view.is_identity` 为真时零开销）：

```python
class Viewport:
    """逻辑台面坐标 -> 桌面父窗口像素。等比缩放 + 居中（letterbox）。"""
    scale: float
    offset: tuple[float, float]
    is_identity: bool
    def to_parent(self, x, y) -> tuple[float, float]
    def to_table(self, x, y) -> tuple[float, float]   # 反向，鼠标输入用
    def length(self, value) -> float

class BilliardsOverlay:
    versus_role: str|None      # 'host' | 'guest' | None
    versus_names: tuple[str, str]   # (自己, 对手)
    def enter_versus(self, session, role: str, names: tuple[str, str]) -> None
    def leave_versus(self, reason: str = '') -> None
```

`enter_versus` 内部：新建 `BilliardsMotion(LOGICAL_BOUNDS, sizes, count=15)`，
`start_versus()`，切换 `self.table`，重算 `rail_frame / ring_frames / ball_canvas`，
隐藏第 15 颗之后的图标窗口，并把 `self.view` 设为信箱缩放。
`leave_versus` 反向恢复到单人台面。

对战期间动画线程每帧：
- 主机：`session.drain()` 处理 `shot/place/restart` → 应用到 `self.table`；
  按 `STATE_HZ` 节流 `session.send({"t":"state", ...,"state":table.snapshot(),"ms":table.play_ms})`。
- 客机：`session.drain()` 取最新 `state` → `table.apply_snapshot()`；
  本机 `can_shoot()` 为真时才允许 `TablePointer` 改 `aim/charge`，击球后把
  `table.pending_shot` 序列化发 `shot`。
- 双方：`status != 'connected'` 时置 `self.disconnect_reason`，由 `ui` 显示。

鼠标输入必须用 `view.to_table()` 反变换；`present()` 的坐标用 `view.to_parent()`；
所有尺寸（球半径、球杆粗细、辅助线宽度、力度条字号）乘 `view.scale`。
`BALL_CANVAS` 固定 128 不够用时按 `ceil(hypot(w,h))+4` 扩大 sprite 画布。

## 7. 界面层接口（`dib/ui.py` / `dib/panel.py` / `dib/versus.py`）

- `MainPanel` 增加一个 pill：`versus`，文案 `联机` / `房间 ABCD` / `等待客机` / `已连接`。
- 新增 `dib/versus.py`：`VersusDialog`（创建房间 / 加入房间两个页签），只依赖
  `netplay.discover / local_addresses / make_code / start_host / join` 与 `tkinter`。
- `ui.py`：`poll()` 里每 50ms 读 `overlay.table`，对战模式把三块统计换成
  `本局用时`、`入袋 X/Y`、`我方 a : b 对手`，并在等待/断线时更新 pill 文案。
- 对战期间禁用「排名」pill（`set_look('rank', ..., enabled=False)`），不提交成绩。
- 断线：`overlay.disconnect_reason` 非空 → 弹一次 `messagebox.showinfo`，
  调 `overlay.leave_versus()` 回到单人，并把 pill 恢复成「联机」。

## 8. 不变式

- 单人模式（`versus=False`）所有行为、数值、界面文案保持原样，`tests/` 现有用例必须全绿。
- 任何模块不得新增第三方依赖。
- 注释与文档用英文（与仓库一致），面向玩家的字符串用中文。
- 不允许改动 `dib/runner.py`、`dib/shell.py`、`dib/instance.py`、`dib/rank.py`、`dib/config.py`。
