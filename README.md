# ogtool — 控制台 25 项功能的脚本版

把参考端控制台那 25 项功能拆成独立脚本，先在新仓库里跑通行为，
之后再决定哪几项回灌进 tdesktop。这一版是 **P0**：底座 + 3 个功能。

25 项里有 3 项不做（充值码 / licence / AI 计费结算，绑他们的服务端），
实际目标 22 个功能模块。**P0 只交 4 个命令**：`accounts`、`mass_send`、
`keep_online`、`auto_reply`。其余 18 个下一轮按 P1/P2/P3 派。

## 一条最重要的规矩：默认 dry-run

所有功能默认 `--dry-run`，真发必须显式 `--live`。
dry-run 不是「跳过整个流程」，而是**完整跑一遍**：
解析目标 → 生成内容 → 入队 → 算间隔与退避 → 逐条打印
（账号 / 目标 / 内容 / 时间戳）→ 打印 `PLAN: N messages, M accounts, ETA X`
→ 退出码 0。只有「真正调用 Telethon 发出去」这一步被 stub 掉。

所以没有 api_id、没有账号、没有代理也能验证全部逻辑，包括
PEER_FLOOD 退避和日限耗尽。

## 安装

```bash
python3 -m venv ~/.venvs/ogtool
~/.venvs/ogtool/bin/pip install -e ".[dev]"   # 或 pip install telethon pyyaml
```

系统 Python 是 PEP 668 管着的，**不要**往系统里装包。

## 配置

```bash
cp config.example.yaml config.yaml
```

`config.yaml` 里的 `${VAR}` 与 `${VAR:default}` 会在读盘时展开，
所以 api 凭据放环境变量，别写进仓库：

```yaml
telegram:
  api_id: ${TG_API_ID}
  api_hash: ${TG_API_HASH}
```

`require_credentials()` 只在 `--live` 路径上调用；dry-run 全程不碰它。

## 四个命令

### accounts — 账号池

```bash
# 手机号登录（dry-run 只登记，不真连）
ogtool accounts add --phone +15551230001 --label demo1

# 导入已有 .session
ogtool accounts add --session /path/to/xxx.session --label seat2

ogtool accounts list
ogtool accounts test            # 连通性；dry-run 下不真连
```

`test` 在 dry-run 下对没有 session 文件的账号报
`SESSION_NOT_FOUND`，但仍然退出码 0 —— 它是汇总式的，不是「一坏全红」。

### mass_send — #5 群发消息

```bash
ogtool mass_send \
  --task-name "demo blast" \
  --targets "@demo_chat,@demo_group,https://t.me/demo_channel" \
  --text "hello" \
  --interval-min 5 --interval-max 9 --group-interval 20 \
  --threads 2 --daily-limit 40 --success-cap 10 --exec-cap 6
```

- 目标：`--targets`（逗号分隔）或 `--targets-file`（每行一个，可重复）。
  支持 `@name`、`t.me/name`、`t.me/c/...`、`+邀请哈希`、数字 id、手机号。
  以 `#` 开头的行当注释丢掉。
- 内容来源三选一：`--text` / `--media` / `--forward-from`；都没给就走
  `ai`（配了 key）或模板降级。
- 账号按 round-robin 交叉排队，每个账号自己的间隔由
  `--interval-min/--interval-max` 抖动，跨群额外等 `--group-interval`。
- 上限四层：单号日限 `--daily-limit`、总成功上限 `--success-cap`、
  总执行上限 `--exec-cap`、线程数 `--threads`。任一挡住就提前收尾。

### keep_online — #21 保持在线

```bash
ogtool keep_online --interval 30 --loops 3
```

按账号错峰发心跳（`beats()`），`--loops 0` 表示一直跑。

### auto_reply — #6 自动回复

```bash
ogtool auto_reply \
  --rule "ping=>pong" --rule "价格=>见置顶" \
  --duplicate-seconds 120 \
  --simulate "@demo_chat|@alice|ping" \
  --simulate "@demo_chat|@alice|ping" \
  --simulate "@demo_chat|@carol|good morning"
```

- `--rule 关键词=>回复`，可重复；关键词是子串匹配，大小写不敏感。
- `--duplicate-seconds` 是重复缓存窗口：同一 (peer, sender, text) 在窗口内
  第二次进来直接 `SKIP: duplicate within 120s`。**必须 > 0**，否则去重
  整体失效（这是 M5-1 那个 blockSignals bug 的同源教训，脚本版在
  `DuplicateCache.Seen` 里显式挡住 `seconds <= 0`）。
- `--simulate peer|sender|text` 用竖线分隔三段的模拟消息，一行一条，
  这样单个 dry-run 就能覆盖「命中 / 重复 / 不命中」三种走法。

## 错误码

发送端的失败统一成一套码，和参考端的命名对齐：

| 码 | 含义 | 可重试 |
|---|---|---|
| `BOT_RESULTS_EMPTY` | 目标返回空结果 | 是 |
| `CHAT_WRITE_FORBIDDEN` | 无发言权限 | 否 |
| `SESSION_NOT_FOUND` | session 文件缺失 | 否 |
| `USER_BANNED_IN_CHANNEL` | 被踢/被封 | 否 |
| `PEER_FLOOD` | 触发风控 | 是（指数退避） |
| `FLOOD_WAIT` | Telegram 明确要求等待 | 是（等指定秒数） |
| `TARGET_INVALID` | 目标解析不了 | 否 |
| `NETWORK_ERROR` | 网络层 | 是 |
| `UNKNOWN` | 兜底 | 否 |

`PEER_FLOOD` 走 `limiter.PeerFloodBackoff`：base 30 s、factor 2、cap 1800 s、
±25% 抖动，`wait_for(attempt)` 取第 `attempt` 次洪泛的等待。

## 落库与日志

- SQLite `og.sqlite`，六张表：`accounts` / `tasks` / `target_lists` /
  `targets` / `results` / `logs`。表结构对齐参考端 `deep.db` 的心智。
- 每任务一份 `logs/<task>/YYYYMMDD.log`（`loggers.task_logger`）。
- dry-run 也会落 `tasks`（`status='dry-run'`）和 `results`（`status='planned'`），
  这样跑完能核对「计划了什么」和「实际发了什么」的差。

## AI

`ai.py` 走 OpenAI 兼容的 `chat/completions`，endpoint / model / key 都可配。
**没配 key 时降级成 `[template] …` 文本，流程照跑**，不会因为没 key 就中断。
这一层只为 #2 / #3 / #4 那几个 AI 项准备，P0 的三个功能不强依赖它。

## 目录

```
ogtool/
  cli.py          argparse 入口，四个子命令
  config.py       YAML + ${VAR} 展开 + Settings/AIConfig/ProxyConfig
  accounts.py     账号池：add / add_session / list / resolve / test
  store.py        SQLite，六张表
  limiter.py      Limits / Budget / Pacer / PeerFloodBackoff
  targets.py      目标解析（用户名 / 链接 / 数字 id / 手机号 / 邀请哈希）
  content.py      内容来源 given / ai / template
  ai.py           OpenAI 兼容客户端，无 key 降级
  senders.py      DryRunSender / TelethonSender + 错误码映射
  loggers.py      每任务一份日志
  runtime.py      RunContext：settings + store + log + sender + pool
  features/       一功能一文件，注册表在 features/__init__.py
tests/            62 条，pytest
```

## 开发

```bash
~/.venvs/ogtool/bin/python -m pytest -q
~/.venvs/ogtool/bin/ruff check .
```

`ruff` 的 line-length 是 88（tdesktop 那边是 80，这边按 Python 习惯放宽）。

## P0 已知缺口

1. **`accounts test` 不是真连通**：dry-run 下只查 session 文件在不在。
   `--live` 才真连，而 P0 没有可用的 api_id，所以这条路径未经端到端验证。
2. **`TelethonSender` 只覆盖了 P0 用到的发送面**：`send_message` / `ping` /
   媒体 / 转发。批量邀请、拉人、改名等要等 P1 再补。
3. **AI 降级路径只验了「无 key」这一侧**：有 key 的真实调用没跑过。
4. **`--threads` 目前是计划期的并发度**（决定账号交叉排队的粒度），
   不是真开线程池并发发送；真并发要等 P1 把 sender 改成可重入。
5. 其余 18 个功能模块未开工，`features/` 里只有 3 个文件。
