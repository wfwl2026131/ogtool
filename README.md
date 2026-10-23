# ogtool — 控制台 25 项功能的脚本版

把参考端控制台那 25 项功能拆成独立脚本，先在新仓库里跑通行为，
之后再决定哪几项回灌进 tdesktop。

25 项里有 3 项不做（充值码 / licence / AI 计费结算，绑他们的服务端），
实际目标 22 个功能模块。**已完成 P0 + P1，共 14 个命令**：

| 命令 | 参考端项 | 命令 | 参考端项 |
|---|---|---|---|
| `accounts` | 账号池 | `batch_invite` | #11 批量拉人 |
| `mass_send` | #5 群发消息 | `filter_phone` | #12 筛选手机 |
| `auto_reply` | #6 自动回复 | `filter_group` | #13 筛选群组 |
| `watch_chat` | #7 监听聊天 | `video_manage` | #14 视频管理 |
| `batch_join` | #8 批量加群 | `proxy` | #15 代理配置 |
| `batch_login` | #9 批量上号 | `add_contact` | #16 批量联系人 |
| `batch_rename` | #10 批量改名 | `batch_invite_v2` | #17 批量拉人v2 |
| `keep_online` | #21 保持在线 | | |

## 三步上手（照这个顺序，否则第一条命令就会撞墙）

```bash
cp config.example.yaml config.yaml                       # 1. 配置文件必须先有
ogtool --config config.yaml accounts add --phone +15551230001 --label demo1
ogtool --config config.yaml mass_send --targets "@demo_chat" --text "hi"
```

两个位置上的坑，写死在这里：

1. **`--config` 是全局选项，必须放在功能名之前**。
   `ogtool mass_send --config x.yaml` 是错的，argparse 会当成未知参数。
2. **任务文件用 `--task`，放在功能名之后**：
   `ogtool --config config.yaml mass_send --task task.example.yaml`。

没有 `config.yaml` 时程序会直接退出码 2，并打印
`run 'cp config.example.yaml config.yaml' first`。

## 一条最重要的规矩：默认 dry-run

所有功能默认 `--dry-run`，真发必须显式 `--live`。
dry-run 不是「跳过整个流程」，而是**完整跑一遍**：
解析目标 → 生成内容 → 入队 → 算间隔与退避 → 逐条打印
（账号 / 目标 / 内容 / 时间戳）→ 打印 `PLAN: N ..., M accounts`
→ 退出码 0。只有「真正调用 Telethon 发出去」这一步被 stub 掉。

所以没有 api_id、没有账号、没有代理也能验证全部逻辑，包括
PEER_FLOOD 退避、日限耗尽、MISSING/FLOOD 导出。

**空账号池不拦**：dry-run 下没有账号时功能降级为「无账号绑定，仅出计划」，
打印 `accounts: 0 (no account bound, plan only)` 后继续，退出码 0。
`--live` 下同样的情形仍是错误（真发必须有 session 在后面）。

## 退出码（自动化要判）

| 码 | 含义 |
|---|---|
| 0 | 全部成功，或 dry-run 计划打印完成 |
| 1 | 跑完了但有条目失败（`failed > 0`） |
| 2 | 用法/配置错误（缺文件、缺目标、参数不合法） |
| 130 | 被 Ctrl-C 中断 |

`accounts test` 也遵守这条：只要有一个账号没通过就返回 1，
不再「一律 0」——以前那样自动化看不出失败。

## 安装

```bash
python3 -m venv ~/.venvs/ogtool
~/.venvs/ogtool/bin/pip install -e ".[dev]"   # 或 pip install telethon pyyaml
```

系统 Python 是 PEP 668 管着的，**不要**往系统里装包。

## 配置

`config.yaml` 里的 `${VAR}` 与 `${VAR:default}` 会在读盘时展开，
所以 api 凭据放环境变量，别写进仓库：

```yaml
api_id: ${OGTOOL_API_ID:0}
api_hash: ${OGTOOL_API_HASH:}
```

`require_credentials()` 只在 `--live` 路径上调用；dry-run 全程不碰它。

## 注入服务端响应（P1 的关键机制）

dry-run 的发送层可以按顺序喂答案，否则退避和分类逻辑没法验：

```yaml
simulate_responses:
  - ok
  - PEER_FLOOD
  - FLOOD_WAIT_120
  - USER_PRIVACY_RESTRICTED
  - CHANNEL_PRIVATE
  - USER_ALREADY_PARTICIPANT
```

也可以从命令行给：`--simulate-responses ok,PEER_FLOOD,FLOOD_WAIT_120`
（这是全局选项，放在功能名之前）。列表用完之后，每个请求都答 `ok`。

能注入的答案分三类：

- **成功**：`ok`
- **可重试**：`PEER_FLOOD`（走指数退避）、`FLOOD_WAIT_<秒>`、`TIMEOUT`、`NETWORK_ERROR`
- **终结**：`USER_PRIVACY_RESTRICTED`、`CHANNEL_PRIVATE`、`USER_ID_INVALID`、
  `CHAT_WRITE_FORBIDDEN`、`INVITE_HASH_EXPIRED`、`USER_NOT_MUTUAL_CONTACT`、
  `CHAT_ADMIN_REQUIRED`、`USER_BANNED_IN_CHANNEL`、`SESSION_NOT_FOUND`
- **分类答案**：`ACTIVE`、`CAN_SEND`、`JOIN_REQUEST`、`CHANNEL`、`USER`
  —— 它们不是错误，是筛选页用来分桶的答案

## 四个 P0 命令

```bash
ogtool accounts add --phone +15551230001 --label demo1
ogtool accounts add --session /path/to/xxx.session --label seat2
ogtool accounts list
ogtool accounts test

ogtool mass_send --task-name "demo blast" \
  --targets "@demo_chat,@demo_group,https://t.me/demo_channel" \
  --text "hello" --interval-min 5 --interval-max 9 --group-interval 20 \
  --threads 2 --daily-limit 40 --success-cap 10 --exec-cap 6

ogtool keep_online --interval 30 --loops 3

ogtool auto_reply --rule "ping=>pong" --duplicate-seconds 120 \
  --simulate "@demo_chat|@alice|ping"
```

## 十个 P1 命令

```bash
# #7 监听聊天：目标 + 排除关键词 + 中转群，dry-run 逐条判断
ogtool watch_chat --targets "@watch1" --exclude "spam,广告" --relay "@relay" \
  --simulate "@watch1|@alice|hello" --simulate "@watch1|@bob|cheap spam"

# #8 批量加群
ogtool batch_join --targets "@g1,@g2,@g3" --request-interval 5 \
  --account-interval 10 --per-account-cap 2 --threads 2

# #9 批量上号：.session + *.json 元数据，ZIP 也行
ogtool batch_login --import-folder ./sessions --label-prefix imp-
ogtool batch_login --import-zip ./bundle.zip
ogtool batch_login --export-ids ./ids.txt
ogtool batch_login --logout frozen          # frozen | all | both

# #10 批量改名：进度/失败/跳过/当前账号/状态，含暂停·继续·停止
ogtool batch_rename --file names.txt --pause-at 2 --stop-at 5

# #11 批量拉人
ogtool batch_invite --groups "@g1" --members "@m1,@m2" --strategy continue \
  --target-success-cap 2 --export-missing ./missing.txt \
  --export-flood ./flood.txt --report

# #12 筛选手机（四分类）
ogtool filter_phone --numbers "+15551110001,+15551110002" --output ./phones.tsv

# #13 筛选群组（六分类，--deep 决定 需验证 桶）
ogtool filter_group --groups "@g1,@g2" --deep --output-dir ./filtered

# #14 视频管理
ogtool video_manage --link "@video" --action join

# #15 代理配置
ogtool proxy --add "socks5://1.2.3.4:1080" --add "http://5.6.7.8:8080"
ogtool proxy --test --timeout-ms 3000
ogtool proxy --bind "demo1=1"

# #16 批量联系人
ogtool add_contact --contacts "@c1,@c2" --per-target-cap 2 \
  --request-interval-ms 1000 --threads 2

# #17 批量拉人v2
ogtool batch_invite_v2 --targets "@my_group" --contacts "@c1,@c2" \
  --success-cap 5 --export-flood-timeout ./flood_timeout.txt
```

批量类统一打印 `processed / total`、`ok`、`failed`、`skipped`，按失败码再分列。

## 六个 P2 命令

```bash
# #0 话术炒群：chat_pattern 脚本回放，A<n> 槽位映射到账号池
ogtool puppet_chat --targets "@g1" --pattern patterns/example.txt \
  --loops 2 --multiplier 2 --daily-limit 20

# #19 话术炒群v2：群主号自发到自己名下，账号错峰启动
ogtool puppet_chat_v2 --targets "@my_group" --pattern patterns/example.txt \
  --startup-interval 10 --loops 1

# #1 复制炒群：采集 → 过滤 → 替换 → 按目标分组排期
ogtool copy_chat --targets "@t1,@t2" --sources "@src" \
  --message "@src|alice|hello spam" --message "@src|bob|clean post" \
  --filter-keywords "spam|广告" --replacements "hello=hi"

# #2 AI原创炒群
ogtool ai_chat --targets "@g1" --interval-min 20 --interval-max 60 \
  --news-channel "@news" --insert-probability 30 --count 3 --topics "周末去哪"

# #3 AI智能评论（比例参数可在 dry-run 里统计核对）
ogtool ai_comment --targets "@channel" --posts 5 --positive-ratio 60 \
  --emoji-probability 30 --per-post-min 1 --per-post-max 3 --seed 7

# #4 AI搬运频道
ogtool ai_clone --sources "@src_channel" --targets "@dst_channel" \
  --mode rewrite --style "简短、口语" --max-items 5 \
  --mapping "@src_channel=@dst_channel"
```

### chat_pattern 脚本格式

一条消息一行，四段不能缺（空正文也要留最后一个 `|`）：

```
[replyPrefix]A<n>|L<n>|D<min>-<max>|<body>
```

| 段 | 含义 |
|---|---|
| `-` 前缀 | 回复层级，每多一层多一个 `-` |
| `A<n>` | **账号槽位**，不是账号 id；运行时按序映射到账号池 |
| `L<n>` | reaction 数 |
| `D a-b` | 延迟秒区间，单值写 `D9-9`（也接受 `D9`） |
| body | 正文；可带 `[STICKER:...]` / `[IMG:文件名]` |

`#` 开头整行是注释，不参与发送。贴纸三种写法都认：
`shortName/x.webp`、`unknown/setId/accessHash/x.webp`、`unknown/setId/x.webp`。
图片相对 `<data>/tdata/chat_pattern/pic/`。zip 导入只取**归档里第一个**
`.txt`，根目录 `pic/` 合并进图片目录，落盘名 `<原名>_<yyyyMMdd_hhmmss>.<ext>`。

## 错误码

| 码 | 含义 | 可重试 |
|---|---|---|
| `BOT_RESULTS_EMPTY` | 目标返回空结果 | 是 |
| `CHAT_WRITE_FORBIDDEN` | 无发言权限 | 否 |
| `SESSION_NOT_FOUND` | session 文件缺失 | 否 |
| `USER_BANNED_IN_CHANNEL` | 被踢/被封 | 否 |
| `PEER_FLOOD` | 触发风控 | 是（指数退避） |
| `FLOOD_WAIT` | 要求等待指定秒数 | 是 |
| `TIMEOUT` | 请求超时 | 是 |
| `TARGET_INVALID` | 目标解析不了 | 否 |
| `NETWORK_ERROR` | 网络层 | 是 |
| `USER_PRIVACY_RESTRICTED` | 对方隐私限制 | 否（进 MISSING） |
| `CHANNEL_PRIVATE` | 私有频道 | 否 |
| `INVITE_HASH_EXPIRED` | 邀请链接过期 | 否 |
| `USER_NOT_MUTUAL_CONTACT` | 非双向联系人 | 否 |
| `UNKNOWN` | 兜底 | 否 |

`PEER_FLOOD` 走 `limiter.PeerFloodBackoff`：base 30 s、factor 2、cap 1800 s、
±25% 抖动。`FLOOD_WAIT_<秒>` 直接用服务端给的秒数。

## 落库与日志

- SQLite `og.sqlite`，八张表：`accounts` / `tasks` / `target_lists` /
  `targets` / `results` / `logs` / `proxies` / `ai_calls`。
- 每任务一份 `logs/<task>/YYYYMMDD.log`。
- dry-run 也落 `tasks`（`status='dry-run'`）与 `results`（`status='planned'`），
  跑完能核对「计划了什么」和「实际发了什么」。
- 导出（MISSING / FLOOD / 筛选结果 / 报表）默认落在 `data/logs/exports/`。

## AI

`ai.py` 走 OpenAI 兼容的 `chat/completions`，endpoint / model / key 都可配。
**没配 key 时降级成 `[template] …`，流程照跑**。

prompt 不写在代码里：模板在 `prompts/*.txt`（`ai_chat` / `ai_comment` /
`ai_clone` / `ai_polish`），`{{占位符}}` 由 `prompts.compose()` 填；任务文件
可用 `prompts: <目录>` 换一套模板。缺模板或缺占位符直接报配置错，不会悄悄用
空串顶上。

每次模型调用落一行 `ai_calls`（任务、账号、kind、prompt、response、tokens、
耗时、成功/失败），跑完能审计。`AIClient(transport=...)` 可注入一个假端点，
测试就是靠它覆盖「正常返回 / 超时 / 报错」三条分支；AI 失败不会让任务挂掉，
按降级文本继续并计数。

## 目录

```
ogtool/
  cli.py          argparse 入口，20 个子命令
  config.py       YAML + ${VAR} 展开
  accounts.py     账号池
  store.py        SQLite，七张表
  limiter.py      Limits / Budget / Pacer / PeerFloodBackoff
  simulate.py     注入的服务端答案
  runner.py       批量循环：上限、重试、退避、MISSING/FLOOD 桶
  progress.py     processed/ok/failed/skipped 计数器
  flags.py        字段描述（CLI 与任务 YAML 共用一份）
  report.py       读名单、写导出、写报表
  targets.py      目标解析
  content.py      内容来源
  ai.py           OpenAI 兼容客户端 + AIResult 审计记录
  chat_pattern.py chat_pattern 脚本解析/渲染/zip 导入
  prompts.py      prompts/*.txt 模板加载与占位符填充
  sources.py      源消息（peer|sender|text|media）与过滤/替换
  senders.py      DryRunSender / TelethonSender + 错误码映射
  loggers.py      每任务一份日志
  runtime.py      RunContext + 空池降级
  features/       一功能一文件，注册表在 __init__.py
patterns/         示例话术脚本
prompts/          外置 prompt 模板（中文文案在这里，不在代码里）
tests/            230 条
```

## 开发

```bash
~/.venvs/ogtool/bin/python -m pytest -q
~/.venvs/ogtool/bin/ruff check .
```

## 已知缺口

1. **`accounts test` 的 `--live` 路径没端到端跑过**：没有可用 api_id，
   dry-run 只查 session 文件在不在。
2. **`TelethonSender` 的 join / invite / profile / contact / participants /
   proxy 只在签名层对齐**，没有真实账号跑过；参数形式按 Telethon 1.43 的
   `client.*` 写，P2 接真号时可能要调。
3. **`batch_login` 不读 `tdata`**：只处理 `.session` + sidecar `*.json`，
   二进制 tdata 解析不在范围内。
4. **AI 的真实 endpoint 没跑过**：无 key 降级、假 transport（正常/超时/报错）
   三条分支都有单测，但没有用真 key 打过 `chat/completions`。
5. **`--threads` 是计划期的并发度**（决定账号交叉粒度），不是真线程池。
6. **`proxy --test` 在 `--live` 下只做 TCP 连通性探测**（socket connect），
   不是真的走代理打 MTProto。
7. **贴纸的 `unknown/setId/...` 两种写法在 `--live` 下不解析**：只有能落到本地
   文件的引用会真的发；unknown 形态 dry-run 照常打印，live 退化成发正文。
8. **#0/#19 的 `A<n>` 槽位重绑只在日限额触发时发生**，不按真实在线状态重排。
9. 其余 5 个功能模块（#18、#20、#22–#24）等 P3。
