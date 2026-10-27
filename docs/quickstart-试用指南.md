# ogtool 试用指南（v1.0.0 自用上手页）

> 目标：从零到一个能用的账号池，再到五个最常用的场景。每条命令先 dry-run
> 看计划，真跑加 `--live`，写东西再加 `--allow-write`。三段式是刻意的：
> 默认永远不碰网络写操作。

<a id="ex-others"></a>

## 0. 安装

```bash
git clone https://github.com/wfwl2026131/ogtool.git
cd ogtool
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
# 可选：tdata 导入路线
pip install "opentele2>=2.0"
ogtool --version        # → ogtool 1.0.0
```

## 1. 填 config.yaml

仓库不带配置，先建一个最小配置（放在你要跑的目录，默认读 `./config.yaml`）：

```yaml
api_id: 12345678          # my.telegram.org → API development tools
api_hash: "你的api_hash"
data_dir: data
session_dir: data/sessions
db: data/og.sqlite
logs: data/logs

proxy:                    # 没有代理就整段留 host: ""
  kind: socks5
  host: "127.0.0.1"
  port: 12888

ai:                       # AI 四件套（ai_chat/ai_comment/ai_clone/puppet_chat）
  base_url: "https://api.openai.com/v1"   # 任何 OpenAI 兼容端点都行
  api_key: "sk-..."
  model: "deepseek-chat"
  timeout: 60
```

- `api_id/api_hash` 只在 `--live` 时读；占位值就能跑全部 dry-run。
- 没配 `ai.api_key` 时 AI 页面自动退到模板话术，流程照走。

## 2. 加账号（三条路线）

```bash
# A. 新号码登录（会问验证码，交互式）
ogtool accounts add --phone +8613800000000 --label t1 --live

# B. 已有 .session 文件直接进池
ogtool accounts add --session /path/to/xxx.session --label t2

# C. 批量导入
ogtool batch_login --import-folder /path/to/sessions/ --live
ogtool batch_login --import-zip /path/to/bundle.zip --live
ogtool batch_login --import-tdata "/path/to/Telegram Desktop/tdata" --live
```

验一遍池子：

```bash
ogtool accounts list
ogtool accounts test --accounts t1 --live     # 连通性 + 谁还活着
```

## 3. 三个闸门（试用前必读）

1. **dry-run 是默认**。不带 `--live` 的任何命令只打印计划，不发一条消息。
2. **`--live` 只开只读**。listen/probe/export/dialogs 这些直接跑。
3. **`--allow-write` 才放开写**。发消息、拉人、改名等页面，没有这个旗子
   会在闸门处直接拒绝退出——这是设计，不是 bug。

限速：`send.daily_limit`（默认 50 条/账号/天）、`account_interval_min/max`
（账号间隔）、`group_interval`（同群间隔，默认 3 秒）。撞到
`FLOOD_WAIT` 时该行记 `FLOOD_WAIT` 码并跳过，不会硬顶——**连续多行
FLOOD_WAIT 就该停手歇账号**，回执里 `codes` 字段一眼能看。

## 4. 五个最常用场景

<a id="ex-mass-send"></a>

### 场景 1：给一组目标群发一条文案（mass_send）

```bash
# 先看计划
ogtool mass_send --accounts t1,t2 \
  --targets "https://t.me/xxxx,https://t.me/yyyy" \
  --text "活动通知……" \
  --interval-min 30 --interval-max 90

# 真发（写操作，双闸门）
ogtool mass_send --accounts t1,t2 \
  --targets "https://t.me/xxxx,https://t.me/yyyy" \
  --text "活动通知……" \
  --interval-min 30 --interval-max 90 --live --allow-write
```

多账号轮发、错峰用 `mass_send_v2`（同参数族）。

<a id="ex-auto-reply"></a>

### 场景 2：私聊自动回复（auto_reply）

```bash
# 带一条规则文件，命中关键词回固定话术；Ctrl+C 或 --run-seconds 结束
ogtool auto_reply --accounts t1 --rules rules.yaml --run-seconds 3600 --live
```

回执按账号逐行落，监听到的每条消息都是一行。

<a id="ex-watch-chat"></a>

### 场景 3：盯群转发（watch_chat）

```bash
# 把 A 群的消息转发到 B 群；--run-seconds 到点自动关回执
ogtool watch_chat --accounts t1 \
  --targets "https://t.me/source-group" \
  --relay "https://t.me/dest-group" \
  --exclude "广告,联系方式" \
  --run-seconds 600 --live --allow-write
```

- 不给 `--relay` 就只看不转（每条消息记一行 skip）。
- `--include`/`--exclude` 关键词过滤；同一条消息 60 秒内去重。

<a id="ex-puppet-chat"></a>

### 场景 4：话术炒群（puppet_chat）

```bash
# 按剧本在群里轮流发言；剧本格式见 ogtool/prompts/ 示例
ogtool puppet_chat --accounts t1,a \
  --group "https://t.me/xxxx" \
  --script script.yaml --live --allow-write
```

<a id="ex-batch-invite"></a>

### 场景 5：批量拉人进群（batch_invite）

```bash
ogtool batch_invite --accounts t1 \
  --groups "https://t.me/xxxx" \
  --members "+8613800000000,+8613900000000" \
  --live --allow-write
```

`USER_NOT_MUTUAL_CONTACT` 是最常见的失败码：双向联系人限制，不是工具错。

## 5. 出问题先看三样

```bash
ogtool accounts test --live                 # 谁的会话还活着
ls -t data/logs/receipts/ | head -5         # 最近 5 张回执
python3 -m json.tool data/logs/receipts/<最新一张>.json
```

回执不变量：`planned == ok + failed`。`codes` 字段是失败码直方图，
`FLOOD_WAIT`/`PEER_FLOOD` 成片就停下休息，别硬跑。

## 6. 风险提示（写给自己）

- 所有写操作过闸门，但**闸门挡不住内容本身**：真机跑发消息就是在真账号
  上发，目标群和文案自己核两遍再回车。
- 新账号头几天低频用：`daily_limit` 先压到 10 以下，间隔拉大。
- 一个 `.session` 同一时刻只能被一个 ogtool 进程持有（撞了会报
  `SESSION_BUSY`），挂机页和别的任务别同时用同一账号。
- tdata 路线会把 tdata 里的账号转成 `.session` 进池，转换是复制不是移动，
  原目录不动。
