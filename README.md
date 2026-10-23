# ogtool — 控制台 25 项功能的脚本版

把参考端控制台那 25 项功能拆成独立脚本，先在新仓库里跑通行为，
之后再决定哪几项回灌进 tdesktop。

25 项里有 3 项不做（充值码 / licence / AI 计费结算，绑他们的服务端），
按总方案的口径是 **22 个功能模块**；加上账号池与设置页参数，一共
**26 个子命令**。P0 / P1 / P2 / P3 四批已全部落地，`pytest` 295 条全绿，
`ruff check .` 无告警。

## 功能总览（交付清单）

| # | 参考端左栏 | 命令 | 关键参数 | dry-run 可验 |
|---|---|---|---|---|
| 0 | 🤖 话术炒群 | `puppet_chat` | `--targets --pattern --loops --multiplier --daily-limit` | 是 |
| 1 | 📋 复制炒群 | `copy_chat` | `--targets --sources --message --filter-keywords --replacements` | 是 |
| 2 | 🧠 AI原创炒群 | `ai_chat` | `--targets --interval-min/max --news-channel --insert-probability --topics` | 是 |
| 3 | 💬 AI智能评论 | `ai_comment` | `--targets --posts --positive-ratio --emoji-probability --per-post-min/max` | 是 |
| 4 | 📦 AI搬运频道 | `ai_clone` | `--sources --targets --mode --style --max-items --mapping` | 是 |
| 5 | 📢 群发消息 | `mass_send` | `--targets --text/--media --interval-min/max --threads --daily-limit` | 是 |
| 6 | 🔄 自动回复 | `auto_reply` | `--rule 关键词=>回复 --duplicate-seconds --simulate` | 是 |
| 7 | 👀 监听聊天 | `watch_chat` | `--targets --exclude --relay` | 是 |
| 8 | 👥 批量加群 | `batch_join` | `--targets --request-interval --per-account-cap --threads` | 是 |
| 9 | 🛅 批量上号 | `batch_login` | `--import-folder/--import-zip --export-ids --logout frozen\|all\|both` | 是 |
| 10 | 🔠 批量改名 | `batch_rename` | `--file --pause-at --stop-at`（暂停/继续/停止） | 是 |
| 11 | 🔂 批量拉人 | `batch_invite` | `--groups --members --strategy --export-missing/--export-flood` | 是 |
| 12 | ⚖️ 筛选手机 | `filter_phone` | `--numbers --threads --output` | 是 |
| 13 | 🧮 筛选群组 | `filter_group` | `--groups --deep --output-dir` | 是 |
| 14 | 🎥 视频管理 | `video_manage` | `--link --action join\|leave` | 是 |
| 15 | 📶 代理配置 | `proxy` | `--add --test --bind label=id --timeout-ms` | 是 |
| 16 | ➕ 批量联系人 | `add_contact` | `--contacts --per-target-cap --request-interval-ms --threads` | 是 |
| 17 | 🧲 批量拉人v2 | `batch_invite_v2` | `--targets --contacts --success-cap --export-flood-timeout` | 是 |
| 18 | 📨 群发私信v2 | `mass_send_v2` | `--contact account\|name\|ref --account-interval-min/max --daily-limit --allow-duplicates` | 是 |
| 19 | 🤖 话术炒群v2 | `puppet_chat_v2` | `--targets --pattern --startup-interval --loops` | 是 |
| 20 | 🧩 自动营销 | `mix_market` | `--group-title --pattern --contact --activity --stop-after N` | 是 |
| 21 | 🟢 保持在线 | `keep_online` | `--interval --loops` | 是 |
| 22 | ✨ 高级功能 | `export_accounts` | `--output-dir --profile label\|id\|username\|device\|2fa --format --no-sessions` | 是 |
| 23 | 📜 自动脚本 | `auto_script` | `--list --script NAME --var k=v --target --on-error stop\|continue` | 是 |
| 24 | ⚙️ 设置 | `settings` | `--json`（只读展示 `globals:` 六个键） | 是 |

底座命令 `accounts`（`add` / `list` / `test`）不在左栏 25 项里，但每个功能都靠它。
**26 条命令全部默认 dry-run**：不传 `--live` 就走完整流程、只把真发那一步 stub 掉。

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

凭据的解析顺序（`ogtool/login.py:resolve_credentials`）：

1. **`$DSH_SECRET_TG_API_CREDENTIALS`**（也认 `$TG_API_CREDENTIALS`）：一个可能
   混着标签文本的 blob。先找带标签的 `api_id\D*(\d{5,12})` +
   `api_hash\D*([0-9a-fA-F]{32})`，找不到再取「第一个 5–12 位纯数字」+
   「第一个 32 位 hex」。
2. `$TG_API_ID` + `$TG_API_HASH`（也认 `$OGTOOL_*`）。
3. `config.yaml` 里的 `api_id` / `api_hash`。

**任何路径都不打印凭据原文**：`api_id` 只出现前两位（`api_id=12***`），
手机号只出现 `+8613****` 这种掩码。这条由 `tests/test_login.py` 里
「blob 进了 env、跑完 stdout+stderr 不含它」的断言守着。

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
ogtool accounts test --live            # 真 get_me()：user_id / username / 手机号掩码 / 受限

# 非交互 live 登录：验证码和 2FA 密码都由外部写文件，进程只轮询
ogtool accounts login --label demo1 --phone +8613800000000 \
  --code-file /tmp/og_code.txt --password-file /tmp/og_pw.txt --timeout 300

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

## 四个 P3 命令

```bash
# #18 群发私信v2：目标来自每个账号自己的联系人，跨账号去重，重跑可续
ogtool mass_send_v2 --text "hello there" \
  --contact "demo1|Ann|@ann" --contact "demo1|Bob|@bob" \
  --contact "demo2|Cid|@cid" --contact "demo2|Ann|@ann" \
  --account-interval-min 5 --account-interval-max 12 --daily-limit 50

# #20 自动营销：五阶段串联（加联系人 → 建群 → 炒群 → 拉人 → 私信）
ogtool mix_market --group-title "Market A" --group-title "Market B" \
  --pattern patterns/example.txt --text "welcome to the market" \
  --contact "Ann|@ann" --contact "Bob|@bob" \
  --activity "@og_market_b=96" --stop-after 5

# 单段验证：只跑到第 2 阶段
ogtool mix_market --group-title "Market A" --pattern patterns/example.txt \
  --text "hi" --contact "Ann|@ann" --stop-after 2

# #23 自动脚本：脚本放 scripts/*.yaml，先列再跑
ogtool auto_script --list
ogtool auto_script --script warm_up --var greeting="hello all" --target "@demo_chat"

# #22 高级功能：账号报表（CSV + JSON）+ 会话归档
ogtool export_accounts --output-dir ./export \
  --profile "demo1|1001|demo_one|iPhone 15|on" --format csv,json

# #24 设置页的非授权参数
ogtool settings
ogtool settings --json
```

`mix_market` 的五个阶段是固定的：`add_contact → create_group →
puppet_chat_v2 → batch_invite_v2 → mass_send_v2`，`--stop-after N` 只跑前 N 段。
`create_group` 建出来的群用标题生成 `@og_<slug>` 引用，供后三段解析。

`auto_script` 的步骤写在 YAML 里，不和任何业务绑死：

```yaml
name: warm_up
title: Warm up a chat
steps:
  - action: log
    message: starting
  - action: join
    target: "{{target}}"
  - action: loop
    times: 2
    steps:
      - action: send_text
        text: "{{greeting}}"
      - action: wait
        seconds: 30
  - action: if
    condition: failed == 0
    then:
      - action: log
        message: "done, ok={{ok}}"
```

原语 11 个：`wait` / `send_text` / `send_media` / `join` / `leave` / `invite` /
`contact_add` / `set_profile` / `if` / `loop` / `log`。每步打印
`[i/N] step=... -> ok|failed`，失败按 `on_error: stop|continue` 走。
解析失败会指名文件与步号，例如
`scripts/x.yaml: steps[2]: unknown action 'teleport' (have: ...)`。

## 非交互 live 登录

给自动化准备的登录路径：进程不读 stdin、不弹任何提示，外部把验证码写进
`--code-file`、把 2FA 密码写进 `--password-file` 即可。

```bash
ogtool --config config.yaml accounts login --label demo1 \
  --phone +8613800000000 --code-file /tmp/og_code.txt \
  --password-file /tmp/og_pw.txt --timeout 300
```

流程与输出行（全部 `flush=True`，逐行可被外部 tail）：

| 行 | 时机 |
|---|---|
| `credentials: api_id=12***` | 凭据解析成功（只打印前两位） |
| `CODE_SENT` | `send_code_request` 已发出，**此时才该跟用户要验证码** |
| `PASSWORD_NEEDED` | 账号开了 2FA，需要写 `--password-file` |
| `LOGIN_OK user_id=… username=@… phone=+8613****` | 登录成功，账号与 profile 已入库 |
| `CODE_INVALID` | 验证码错，最多重试 2 次（共收 3 个码） |
| `PHONE_CODE_EXPIRED` | 码过期，要重新发 |
| `FLOOD_WAIT_<秒>` | 被限流，打印剩余秒数后退出 |
| `TIMEOUT_NO_CODE` / `TIMEOUT_NO_PASSWORD` | 等到 `--timeout` 没等到文件 |

细节：

- 轮询间隔 1 秒，文件**读到第一行就删掉**（防重复消费）。
- 退出码：成功 `0`、登录失败 `1`、凭据缺失或用法错 `2`。
- `accounts test --live` 同样走真 `get_me()`，输出
  `user_id=… username=@… phone=+8613**** restricted=no`；没有 session 文件仍是
  `SESSION_NOT_FOUND`。

## 全局参数（`globals:`）

设置页里的非授权项落在 `config.yaml` 的 `globals:` 段，`settings` 命令只读展示：

| 键 | 参考端标签 | 默认 | 说明 |
|---|---|---|---|
| `account_batch` | 账号分批加载数量 | 20 | 账号池按这个条数分页拉取 |
| `threads_max` | 全局线程池最大线程数 | 128 | 单任务 `--threads` 的封顶 |
| `resume_delay` | 恢复任务延迟 | 0.0 | 秒；只做配置与校验 |
| `tp_overload` | TP超载值 | 0 | 0 = 关闭超载检查 |
| `restart_interval` | 重启间隔 | 0.0 | 只算「该不该重启」，不实现自杀重启 |
| `restart_timed` | 定时重启 | false | 同上 |

充值码 / 到期时间 / licence 一律不做。

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

- SQLite `og.sqlite`，九张表：`accounts` / `tasks` / `target_lists` /
  `targets` / `results` / `logs` / `proxies` / `ai_calls` / `profiles`。
  `profiles` 存 #22 报表要用的 user id / username / 设备 / 2FA / 最后活跃。
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
  cli.py          argparse 入口，26 个子命令
  config.py       YAML + ${VAR} 展开
  accounts.py     账号池
  store.py        SQLite，九张表
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
tests/            295 条
```

## 开发

```bash
~/.venvs/ogtool/bin/python -m pytest -q
~/.venvs/ogtool/bin/ruff check .
```

## 已知缺口

1. **`accounts login` / `accounts test --live` 还没用真号跑过**：代码路径、
   Telethon 1.45 的 `sign_in` / `send_code_request` 签名、错误类名都已核对，
   全部行为由 mock client 覆盖（20 条测试，含 2FA、错码重试、超时、限流、
   凭据不落日志）；真实验证要等 api 凭据 + 测试号。
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
9. **`export_accounts` 不做 tdata 二进制转换**：报表里明确写
   `not_implemented`，原因是要自己写 TDATA 二进制格式，超出脚本版范围。
10. **`mix_market` 的阶段 3 会另开一条 `task` 行**（名字 `<task>:3`），
    阶段 1/2/4/5 直接调 sender，不入 `results`；只有死群记 `DEAD_CHAT`。
11. **`create_group` 在 dry-run 里返回 `channel:<标题>`**；真机若频道没有
    username 会返回数字 id，`@og_<slug>` 只是为了让后三段能解析。
12. **`restart_interval` / `restart_timed` 只做配置与校验**，不实现自杀重启。
13. **`auto_script` 的 `if` 只支持四个计数器**（`ok` / `failed` / `skipped` /
    `steps`）与整数比较；`loop` 上限 1000，防止写错把任务挂死。
