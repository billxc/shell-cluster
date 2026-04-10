# CJK IME 测试指南

> 本文档说明如何运行和维护 Shell Cluster 的 CJK 输入法测试。
> 调研报告见 `.harness/CJK-IME-Report.md`。

---

## 文件总览

```
tests/ime/                          # 测试工具和数据
  start_test_server.py              # 测试用 shell server（带 PTY 收发日志）
  recorder.html                     # IME 事件录制器
  replay.html                       # 事件回放验证页
  test_runner.html                  # 录制事件自动回放测试
  test_cjk_backend.py              # 后端 CJK 回环测试（Python WebSocket）
  fixtures/                         # 录制的 IME 事件 JSON
    mixed_pinyin_capslock_toggle.json
    terminal_commands_ime_active.json

src/shell_cluster/dashboard_v2/static/
  ime_bug_test.html                 # Bug 回归测试（5 个断言，TDD）
  ime_test.html                     # 前端 onData 输出验证
  ime_e2e_test.html                 # 端到端测试（xterm → WebSocket → PTY）
  ime_live_test.html                # 手动测试页（实时日志）
```

---

## 快速开始

### 1. Bug 回归测试（推荐，最常用）

验证已知的 5 个 CJK/IME bug 是否已修复。修复前应全部 FAIL，修复后全部 PASS。

```bash
# 启动测试 shell server（与生产隔离，端口 18765）
python3 tests/ime/start_test_server.py 18765 &

# 启动 HTTP 服务
cd src/shell_cluster/dashboard_v2/static
python3 -m http.server 9877 &

# 浏览器打开
open http://localhost:9877/ime_bug_test.html

# 或 Headless 运行（需要 puppeteer）
cd /tmp && node ime_bug_test_run.js
```

测试内容：

| # | 测试名 | 验证什么 | 修复文件 |
|---|--------|---------|---------|
| 1 | `unicode11_loaded` | `@xterm/addon-unicode11` 已加载，`activeVersion='11'` | `app.js`, `index.html` |
| 2 | `cjk_font_in_stack` | `fontFamily` 包含 CJK 字体 | `app.js` |
| 3 | `isComposing_guard` | document keydown 在 IME 组合期间不触发 | `app.js` |
| 4 | `custom_key_handler` | `attachCustomKeyEventHandler` 已注册 | `app.js` |
| 5 | `pty_utf8_locale` | PTY 环境包含 UTF-8 locale | `manager.py` |

### 2. 手动测试（Live Test）

连接真实 PTY，实时查看 JS 侧和 PTY 侧的 I/O 日志。

```bash
# 启动带日志的 shell server
python3 tests/ime/start_test_server.py 18765 &

# 启动 HTTP 服务
cd src/shell_cluster/dashboard_v2/static
python3 -m http.server 9877 &

# 打开测试页
open http://localhost:9877/ime_live_test.html
```

页面左侧是终端（连接测试 PTY），右侧实时显示：
- `[onData→PTY]` — xterm 发送给 PTY 的内容
- `[compositionstart/update/end]` — IME 组合事件
- `[keydown]` — IME 期间的按键（keyCode=229）

PTY 侧日志写入 `tests/ime/pty_log.jsonl`，格式：
```json
{"ts":1234567890.0, "dir":"input",  "session":"xxx", "text":"你好", "hex":"..."}
{"ts":1234567890.1, "dir":"output", "session":"xxx", "text":"你好\r\n", "hex":"..."}
```

### 3. 前端 onData 测试

回放录制的 IME 事件，验证 xterm.js `onData` 输出是否正确。不需要 shell server。

```bash
cd src/shell_cluster/dashboard_v2/static
python3 -m http.server 9877 &
open http://localhost:9877/ime_test.html
```

### 4. 后端 CJK 测试

通过 WebSocket 直接发送 CJK 文本，验证 server → PTY → echo 回环正确。

```bash
# 需要 shell server 运行中
python3 tests/ime/test_cjk_backend.py ws://localhost:18765
```

测试用例：中文/日文/韩文/混合文本/CJK 标点/4096 字节边界。

---

## 录制新的 IME 事件

当需要测试新的输入法场景时：

1. 打开录制器：
   ```bash
   cd src/shell_cluster/dashboard_v2/static
   python3 -m http.server 9877 &
   open http://localhost:9877/../../../tests/ime/recorder.html
   # 或直接 file:// 打开：
   open tests/ime/recorder.html
   ```

2. 点击 **Start Recording**，在终端区域用输入法打字

3. 点击 **Stop Recording**，填写 Expected Output 和 Description

4. 点击 **Download JSON**，保存到 `tests/ime/fixtures/`

5. 将 fixture 的 `events` 和 `expectedOutput` 添加到 `ime_test.html` 的 `FIXTURES` 数组

### Fixture JSON 格式

```json
{
  "version": 1,
  "metadata": {
    "description": "场景描述",
    "browser": "Chrome/146.0.0.0",
    "os": "MacIntel",
    "imeMethod": "macOS Chinese Pinyin",
    "xtermVersion": "6.0.0",
    "recordedAt": "2026-04-10T15:42:56Z"
  },
  "events": [
    {
      "t": 0,            // 相对时间戳 (ms)
      "type": "keydown", // DOM 事件类型
      "key": "n",        // KeyboardEvent.key
      "code": "KeyN",    // KeyboardEvent.code
      "keyCode": 229,    // 229 = IME 处理中
      "isComposing": true
    },
    {
      "t": 1,
      "type": "compositionupdate",
      "data": "n"        // 当前组合文本
    }
  ],
  "expectedOutput": "你好"  // 期望的 onData 拼接结果
}
```

---

## 测试 Shell Server

`tests/ime/start_test_server.py` 启动一个独立的 shell server，与生产环境完全隔离：

- 默认端口 **18765**（生产用 8765 或动态端口）
- 带 PTY 收发日志（`pty_log.jsonl`）
- 用法：`python3 tests/ime/start_test_server.py [port]`

日志格式（JSONL，每行一个 JSON）：
- `dir: "input"` — JS → PTY（用户输入）
- `dir: "output"` — PTY → JS（shell 输出）
- 包含 `text`（UTF-8 解码）和 `hex`（原始字节）

---

## 关键实现细节

### Synthetic CompositionEvent 的局限

浏览器不允许 synthetic `CompositionEvent` 自动更新 `textarea.value`。
而 xterm.js 的 `CompositionHelper._finalizeComposition` 通过 `setTimeout(0)` 从
`textarea.value` 读取最终组合文本。

解决方案（用于 ime_test.html 和 ime_e2e_test.html）：
1. 在 dispatch composition/input 事件前，手动设置 `textarea.value = evt.data`
2. 在 `compositionend` 后 `await setTimeout(20ms)` yield 控制权，
   让 xterm 的延迟回调在下一个事件前执行完毕

这是 Playwright/Puppeteer 模拟 IME 的标准做法。

### Headless 运行

所有测试页都将结果写入 `document.body.dataset.results`（JSON 格式）
和 console（`[IME-BUG]`/`[IME-TEST]` 前缀），便于 Puppeteer 等 headless 工具读取。

---

## 已修复的 Bug

| # | Bug | 文件 | 改动 |
|---|-----|------|------|
| 1 | unicode11 addon 未加载 | `app.js`, `index.html` | 加载 addon-unicode11.min.js，设 activeVersion='11' |
| 2 | fontFamily 无 CJK 字体 | `app.js` | 追加 PingFang SC / Noto Sans CJK SC / Microsoft YaHei |
| 3 | isComposing 守卫缺失 | `app.js` | keydown handler 加 `if (e.isComposing \|\| e.keyCode === 229) return` |
| 4 | customKeyEventHandler 未注册 | `app.js` | 创建 Terminal 后调用 `attachCustomKeyEventHandler` |
| 5 | PTY locale 未设 UTF-8 | `manager.py` | `env.setdefault("LANG", "en_US.UTF-8")` |
| 6 | os.closerange 溢出 | `manager.py` | cap `SC_OPEN_MAX` 到 65536（Python 3.14 兼容） |
