# ogtool — 控制台 25 项功能的脚本版

把参考端控制台那 25 项功能拆成独立脚本，先在新仓库里跑通行为，
之后再决定哪几项回灌进 tdesktop。

**当前版本 v1.0.0**（2026-10-27）：25 项功能全部接上真机——23 项有
真机回执落盘，video_manage（真开群通话 join+speak）与 watch_chat
（真监听+转发）在第 10 阶段闭环；tdata 导入路线用真机会话做过
session→tdata→session 往返验证（`batch_login-4672` 回执）。

25 项里有 3 项不做（充值码 / licence / AI 计费结算，绑他们的服务端），
按总方案的口径是 **22 个功能模块**；加上账号池与设置页参数，一共
**28 个子命令**。P0 / P1 / P2 / P3 四批已全部落地，`pytest` 全绿，
`ruff check .` 无告警。MIT 许可，见 [LICENSE](LICENSE)。

> 本仓库不带任何账号、会话或私群链接：`config.yaml`、`data/`、会话文件
> 都在 `.gitignore` 里；`scripts/live_smoke.py` 的夹具群链接从
> `data/fixture_group`（gitignored）读取，没有该文件时相关用例自动跳过。
> 自带 `config.example.yaml`，三步上手照 README 走即可。

## 功能总览（交付清单）

| # | 参考端左栏 | 命令 | 关键参数 | dry-run 可验 | 示例 |
|---|---|---|---|---|---|
| 0 | 🤖 话术炒群 | `puppet_chat` | `--targets --pattern --loops --multiplier --daily-limit` | 是 | [场景4](docs/quickstart-试用指南.md#ex-puppet-chat) |
| 1 | 📋 复制炒群 | `copy_chat` | `--targets --sources --message --filter-keywords --replacements` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 2 | 🧠 AI原创炒群 | `ai_chat` | `--targets --interval-min/max --news-channel --insert-probability --topics` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 3 | 💬 AI智能评论 | `ai_comment` | `--targets --posts --positive-ratio --emoji-probability --per-post-min/max` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 4 | 📦 AI搬运频道 | `ai_clone` | `--sources --targets --mode --style --max-items --mapping` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 5 | 📢 群发消息 | `mass_send` | `--targets --text/--media --interval-min/max --threads --daily-limit --loop-rounds --loop-pause` | 是 | [场景1](docs/quickstart-试用指南.md#ex-mass-send) |
| 6 | 🔄 自动回复 | `auto_reply` | `--rule 关键词=>回复 --duplicate-seconds --simulate` | 是 | [场景2](docs/quickstart-试用指南.md#ex-auto-reply) |
| 7 | 👀 监听聊天 | `watch_chat` | `--targets --exclude --relay` | 是 | [场景3](docs/quickstart-试用指南.md#ex-watch-chat) |
| 8 | 👥 批量加群 | `batch_join` | `--targets --request-interval --per-account-cap --threads --resume --verify-url/--verify-rounds/--verify-delay` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 9 | 🛅 批量上号 | `batch_login` | `--import-folder/--import-zip --export-ids --logout frozen\|all\|both` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 10 | 🔠 批量改名 | `batch_rename` | `--file --pause-at --stop-at`（暂停/继续/停止） | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 11 | 🔂 批量拉人 | `batch_invite` | `--groups --members --strategy --export-missing/--export-flood` | 是 | [场景5](docs/quickstart-试用指南.md#ex-batch-invite) |
| 12 | ⚖️ 筛选手机 | `filter_phone` | `--numbers --threads --output` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 13 | 🧮 筛选群组 | `filter_group` | `--groups --deep --output-dir` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 14 | 🎥 视频管理 | `video_manage` | `--link --action join\|leave` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 15 | 📶 代理配置 | `proxy` | `--add --test --bind label=id --timeout-ms` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 16 | ➕ 批量联系人 | `add_contact` | `--contacts --per-target-cap --request-interval-ms --threads` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 17 | 🧲 批量拉人v2 | `batch_invite_v2` | `--targets --contacts --success-cap --export-flood-timeout --resume --monitor-group/--monitor-seconds` | 是 | [场景5](docs/quickstart-试用指南.md#ex-batch-invite) |
| 18 | 📨 群发私信v2 | `mass_send_v2` | `--contact account\|name\|ref --account-interval-min/max --daily-limit --allow-duplicates` | 是 | [场景1](docs/quickstart-试用指南.md#ex-mass-send) |
| 19 | 🤖 话术炒群v2 | `puppet_chat_v2` | `--targets --pattern --startup-interval --loops --patterns --cycles --resume --listen` | 是 | [场景4](docs/quickstart-试用指南.md#ex-puppet-chat) |
| 20 | 🧩 自动营销 | `mix_market` | `--group-title --pattern --contact --contacts-file --activity --stop-after N` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 21 | 🟢 保持在线 | `keep_online` | `--interval --loops` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 22 | ✨ 高级功能 | `export_accounts` | `--output-dir --profile label\|id\|username\|device\|2fa --format --no-sessions` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 23 | 📜 自动脚本 | `auto_script` | `--list --script NAME --var k=v --target --on-error stop\|continue` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| 24 | ⚙️ 设置 | `settings` | `--json`（只读展示 `globals:` 六个键） | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |
| T1 | 🌐 翻译工作台 | `translate` | `--text --texts-file --lg-from --lg-to --strategy --azure-key --quota-total --batch-size --list` | 否 | [指南](docs/quickstart-试用指南.md#ex-others) |
| T2 | 👑 群管家 | `group_manage` | `--groups --groups-file --from-store --target --password --password-env --interval-ms` | 是 | [指南](docs/quickstart-试用指南.md#ex-others) |

底座命令 `accounts`（`add` / `list` / `test` / `password` / `passkey` / `passkey-login`）与运维命令 `runs` / `quota`（日限落盘的读表与修复）不在左栏 25 项里，但每个功能都靠它们。
**28 条命令全部默认 dry-run**：不传 `--live` 就走完整流程、只把真发那一步 stub 掉。

## 对齐参考客户端的四个进阶能力（v1.1.1+）

| 能力 | 命令 | 说明 |
|---|---|---|
| 循环群发 | `mass_send --loop-rounds 3 --loop-pause 600` | 每轮重新规划（预算/内容重算），轮次间隔可调；`--loop-rounds 0` 跑到 Ctrl+C；某一轮计划为 0 自动收工 |
| 邀请监视 | `batch_invite_v2 --monitor-group <群> --monitor-seconds 0` | 蹲群等新人，进一个邀一个；首轮名单当基线不重复邀请；`--monitor-invite-all` 连老人一起邀 |
| 断点续跑 | `batch_join --resume` / `batch_invite_v2 --resume` | 状态就是回执库：跳过已成功的（账号, 目标）/（handler, 群<-联系人），中断后接着跑不重发 |
| 加群后验证 | `batch_join --verify-url <webhook> --verify-rounds 3 --verify-delay 5` | 每次进群成功后 POST `{feature,task,account,target}`，回 200 = 验证通过；不通重试，最终失败写 VERIFY_FAILED 回执行 |
| 每账号代理绑定（v1.1.8+） | `proxy --add socks5://host:port --bind 账号=代理ID` | 代理页的"分配"落地：绑定后该账号的客户端直连绑定代理，未绑定的账号走 `config.yaml` 的全局 `proxy:`；绑定值解析不了就退回全局并打一行 PROXY 提示 |
| 动态代理+轮换（v1.1.9+） | `proxy --add 'socks5://user:pwd_session-{SessionKey}_lifetime-10m@host:port' --bind 账号=ID`；`proxy --test --rotate` | `{SessionKey}` 模板按账号各自展开成粘性会话键（8 位字母数字；`#12`=12 位；`#d#12`=纯数字），一个账号一个出口 IP；`--rotate` 把测速超时代理上的账号挪到最快的可用代理（对齐参考端 ProxyRotationManager） |
| 傀儡聊天执行器（v1.2.0+） | `puppet_chat_v2 --patterns a.txt --patterns b.txt --cycles 3 --message-wait 7 --stats-dir data/puppet_chat --resume --listen 120` | 对齐参考端 `PuppetChatExecutor`：一个任务吃多份脚本、整组循环 N 轮（轮间停 `--cycle-interval` 秒）、每轮写统计 `puppet_chat_<task>_<cycle>.json`；`--message-wait` 是两条消息之间的保底秒数（对齐 `Waiting %1 seconds before next message`）；`--resume` 以回执库为准跳过已发出的（槽位， 目标）对，中断重跑不重发；槽位映射带日限自动换号与死号整组迁移（对齐 `Daily speech limit reached` / `mark account unavailable`）；`--listen N` 收尾开 N 秒收音窗，每个绑定号挂 handler 记录进群消息，本方傀儡号自己的消息丢弃（对齐 `speechLogged` / `Filter messages from internal puppet accounts`） |
| 混市工作流编排（v1.2.1+） | `mix_market --contacts-file data/inputs/contacts.txt --group-title "A" --stop-after 5` | 对齐参考端 MixMarket（:1741560-1741650）：启动打 `Workflow:` 五步原文块 + `Workflow started`/`Workflow stopped`；`--contacts-file` 即 `inputFilePath`，文件缺/空按页面文案报 `Input file not found: %1` / `Input file is empty`，行并进 `--contact` 之后；五步各带权限门——某步零成功且失败码全是权限类（CHAT_WRITE_FORBIDDEN 等）时打 `<步名>: No permission` 并把失败转为 skipped（步没跑，不是干砸）；`heldTargets` 落盘 `mix_market_held_targets.json`：建群失败的标题留档，下次启动先重试再清档（`held: N target(s) restored` / `heldTargets: N kept on file`）；`--create-rest-min > --create-rest-max` 报 `Invalid rest range`；逐步日志照抄参考端 `A%1 add contacts/added/failed (%3)`、`A%1 creating owned %2`/`target created`、`[Speech]`、`[Invite]`、`Batch Pull v2 error: %1`、`Mass Send DM v2 completed`、`Dead targets detected, rebuild accounts: %1`、`GroupMgr config missing, use defaults.` |
| 翻译工作台（v1.3.0+） | `translate --text "你好" --lg-to en --strategy ai`；批量 `--texts-file lines.txt --batch-size 20`；Azure `--strategy azure --azure-key KEY` | 对齐参考端右侧工具栏翻译页（:1740461 RightToolBar，10 类全家桶）：手动框（`setupManualTranslateSection`/`onTranslateClicked`）对应 `--text`；`TranslationBatchManager` 对应 `--texts-file` 分块批量 + 批量缓存查询 `input IN (%1) AND lg_to = ?`（:1723889）；历史表沿用参考端主键 `(input, lg_to)`（:1723409），重译覆盖不增行；配额状态机三句判决照抄（`Translation quota is sufficient` / `WARNING: Translation quota running low...` / `CRITICAL: Translation quota exhausted! Translation will be blocked.`，:1737723-1737725），预算耗尽打 `Translation continuation blocked due to quota issues`（:1737735）并跳过余量；用量跟踪带 FromCache 拆分（`Recorded usage - Length: %1, FromCache: %2...`，:1737659）+ 周期上报（`Usage reported successfully`，失败 `data preserved for next retry` :1737734）；周期到期 `Current period usage reset`（:1737726）；`--strategy azure` 走真 Azure REST，映射其自家错误词 `Quota exceeded`/`Subscription disabled`（:1737778-1737779）；无钥匙打 `No keys available, translation service will use fallback`（:1737741）+ 维护横幅 |
| 群主转让（v1.3.1+） | `group_manage --groups "@g1,-1001234" --target acc2 --password "$TWOPASS"`；存量 `--from-store`；批量 `--groups-file groups.txt` | 对齐参考端 Group Manager 页（控制台 "Open Group Manager" 打开的 ex 页，:1742778）：原子端点 `tg.answer_group.transfer_owner_and_leave`（:1743915）自带退群语义，`auto_transfer_ok`/`auto_transfer_failed` 日志行照抄（:1743960-1743961），落库沿用参考端 `group_mgr` 表（:1723421，group_id PK / owner_phone / invite_link / status / chat_type / old_owner）。每群三步：新 owner 先入群——目标是自有账号就自己 join、是外部用户就源账号 invite，失败打 `Target account failed to join group %1 before transfer.`；转让要源账号二步密码（`EditCreator`/`messages.editChatCreator`，SRP `compute_check`），缺密码整号跳过打 `Skip account %1: missing second password.`；成功后原 owner 退群 `Original owner left group %1.`（退群失败不影响转让，`left=0`）。无效群 id 打 `Invalid group id %1, skip transfer.`；`--from-store` 按 `owner_phone` 从 `group_mgr` 表取名下群，与手填去重 |
| 日限落盘（v1.3.2+） | `quota --namespace puppet_chat --account acc1`；修复 `quota --reset --namespace puppet_chat --account acc1 --yes` | 对齐参考端 QuotaDiskWriter（:2265509）——`flushToDisk`（:2241358）/`isLimitReachedForNamespace`（:2241357），实例化为 `ChatDailyQuota`（:2184666）与 `MassSendDailyQuota`（:2184694）。per-account 日限计数落 `daily_quota` 表（namespace/account/day 主键，success/attempt 两列——:1737953 的 `Success limit: %2, Attempt limit: %3` 两帽检查），重启读表不归零。接线的页：`puppet_chat`/`puppet_chat_v2`（SlotMap 预算种子化 + live 结果写回）、`copy_chat`（budget 种子化 + 写回）；`mass_send`/`mass_send_v2` 原本就走 results 表兜底。全耗尽判决行照抄：`All %1 accounts reached per-account daily limits (success=%2, attempt=%3), wait until tomorrow`（:1737966）、`All selected accounts reached today's limits; will retry at %1...`（:1737953）。`quota --reset` 至少要一个过滤器（namespace/account），不带 --yes 只打计划；干跑永不写计数 |
 | 批量改密码（v1.3.3+） | `accounts password --accounts acc1 --new-password-file np.txt`；带旧密码 `--current-password-file op.txt`；真改加 `--live --allow-write` | 对齐参考端批量改密码页（console ex 页 `batch_modify_password`，:1737291-1737305）。旧密码走页面自己的三源模型：`--current-password-file` 的箱内默认先答（`input old password`），没有就读库里的 per-account 记录（`metadata old password`，accounts 表新 `password` 列），都没有就当账号无 2FA（`empty old password`，`current_password=None`）。成功行照抄 `Account %1 modify successfully via %2`（%2 = 答对的那个源），同步判决照抄三句：库记录写回 = `password updated and metadata synced.`（:1737302）、session 旁有 sidecar `.json` 一并写回 = `and JSON synced.`（:1737303）、库写失败 = `(metadata sync skipped).`（:1737304）——下次运行同一账号就能用自己的记录答 `via metadata old password`。失败行照抄 `Account %1 failed to change password.` + `Account %1 (%2) error: %3`；任务守卫照抄 `Password change task started.` / `Password change task is running, please stop it first.`（:1737294-1737295，live 且 10 分钟内有同任务在跑才拦，防崩溃残行锁死）；Ctrl+C = 页面的 Stop，打 `Stop requested...` 按整批计划收 `Success: %1/%2`。密码值永不进日志/回执/终端 |
 | passkey 导出（v1.3.3+） | `accounts passkey --accounts acc1 --out-dir data/passkeys --live --allow-write` | 同一页的第二个任务（:1737294 `Passkey export task started.`）：每账号一个 `.passkey` JSON 元数据文件（`passkey<label>_<时间戳>.passkey`，:1737371 `passkey%1_%2` 命名式），横幅照抄 `System WebAuthn is unavailable in this build; exporting metadata-only passkey files.`（:1737382）——本构建只做 metadata 导出、私钥回放未实现（:1737375），无手机号整号跳 `Account %1 has no phone number, skipped.`（:1737379）；空池打页面守卫 `Please select at least one account.`（:1737293）；收尾 `Passkey export finished. Success: %1/%2`（:1737385）。文件字段用参考端登录侧校验的键名 `PhoneNumber`（:1737361），导出可直接喂登录任务 |
  | passkey 登录（v1.3.4+） | `accounts passkey-login --files a.passkey b.passkey`；整夹扫描 `--folder data/passkeys`；真跑加 `--live --allow-write` | 对齐参考端 batch_login 页的 passkey 分支。Start 门分类器（:1737360-1737363 `passkey`/`json`/`session/json`/`mixed`/`none`）：空选 `Please select account files or folder`（:1742494）、文件不存在 `Account file does not exist: %1`（:1742500）、混选 `Mixed .passkey and session/json inputs are not supported.`（:1742499）全部开跑前拒绝；纯 session/json 仍然开任务并收 `No .passkey files found.` + `Passkey login finished with no files.`（:1737372-1737373）。读到文件：横幅 `Starting passkey login task`（:1742503）→ `Detected %1 .passkey file(s).`（:1737374）→ 构建边界两行照抄（:1737375-1737376）→ 每文件读校验三线（`Failed to open passkey file: %1` / `Invalid passkey file format: %1` / `Passkey file missing PhoneNumber: %1`，:1737357-1737361）→ 合法记录仍拦 `Passkey login blocked for %1.passkey: secure replay is not implemented in this build.`（:1737377）→ 收尾 `Passkey login finished. Success: %1/%2`（:1737378）。dry-run 一行计划不建目录 |

## 三步上手（照这个顺序，否则第一条命令就会撞墙）

```bash
cp config.example.yaml config.yaml                       # 1. 配置文件必须先有
ogtool --config config.yaml accounts add --phone +15551230001 --label demo1
ogtool --config config.yaml mass_send --targets "@demo_chat" --text "hi"
```

**不想背命令？交互式向导全覆盖 25 个功能页**（挑功能 → 填参数 → 先
dry-run → 确认才真跑；菜单按 ⚠写入/只读 分组，写操作二次确认加
--allow-write）：

```bash
ogtool-wizard
```

更完整的自用手册（加账号三条路线、五个常用场景的 dry-run/live/
allow-write 三档命令、限速与 FLOOD_WAIT 规则）在
[docs/quickstart-试用指南.md](docs/quickstart-试用指南.md)。

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
# #7 监听聊天：目标 + 排除关键词 + 中转群
# dry-run 逐条回放判断；--live 真挂 NewMessage handler，Ctrl+C 收尾（任务记 done）
ogtool watch_chat --targets "@watch1" --exclude "spam,广告" --relay "@relay" \
  --simulate "@watch1|@alice|hello" --simulate "@watch1|@bob|cheap spam"
ogtool --config config.yaml watch_chat --accounts t1 \
  --targets "@watch1" --exclude "spam" --relay "@relay" --live

# #8 批量加群
ogtool batch_join --targets "@g1,@g2,@g3" --request-interval 5 \
  --account-interval 10 --per-account-cap 2 --threads 2

# #9 批量上号：.session + *.json 元数据，ZIP 也行
ogtool batch_login --import-folder ./sessions --label-prefix imp-
ogtool batch_login --import-zip ./bundle.zip
ogtool batch_login --import-tdata ./tdata   # Telegram Desktop 账号,见下
ogtool batch_login --export-ids ./ids.txt
ogtool batch_login --logout frozen          # frozen | all | both

# #9 的 tdata 路线:二进制格式不手写,可选依赖转换
#   pip install "ogtool[tdata]"        (即 opentele2)
# 转出的会话逐账号落行,收据与 .session 路线完全一致。
# 没装转换器时命令直接给出上面的安装提示,退出码 2。

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

# #19 执行器形态（v1.2.0）：对齐参考端 PuppetChatExecutor
#   多份脚本 + 整组循环 + 每轮统计 + 断点续跑 + 收尾收音
ogtool puppet_chat_v2 --targets "@my_group" \
  --pattern patterns/a.txt --patterns patterns/b.txt --patterns patterns/c.txt \
  --cycles 3 --cycle-interval 300 --message-wait 7 \
  --stats-dir data/puppet_chat --resume --listen 120

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

# #20 工作流形态（v1.2.1）：对齐参考端 MixMarket 五步工作流
#   联系人输入文件（inputFilePath）+ 建群失败目标跨轮持久（heldTargets）
ogtool mix_market --group-title "Market A" \
  --contacts-file data/inputs/contacts.txt --pattern patterns/example.txt \
  --text "welcome" --stop-after 5

# #T1 翻译工作台（v1.3.0）：手动一条 → 批量文件 → 带配额防跑飞
ogtool translate --text "hello there" --lg-to zh
ogtool translate --texts-file data/inputs/lines.txt --lg-to zh \
  --batch-size 20 --quota-total 500000 --report-interval 60
ogtool translate --strategy azure --azure-key "$AZURE_KEY" \
  --texts-file data/inputs/lines.txt --lg-to zh
ogtool translate --list --search hello --limit 50

# #T2 群管家（v1.3.1）：把群主转给自己的号，转完原号退群
ogtool group_manage --groups "@g1,-1001234" --target acc2 --password "$TWOPASS"
ogtool group_manage --groups-file data/inputs/groups.txt \
  --target acc2 --password-env TWOPASS --interval-ms 2000
ogtool group_manage --from-store --target acc2 --password "$TWOPASS"

# 底座 quota（v1.3.2）：日限计数落盘，重启不归零（QuotaDiskWriter 语义）
ogtool quota --namespace puppet_chat
ogtool quota --day 2026-10-29 --account acc1
ogtool quota --reset --namespace puppet_chat --account acc1 --yes

# 底座 accounts password（v1.3.3）：批量改 2FA 云密码，三源旧密码自动降级
ogtool accounts password --accounts acc1 --new-password-file /tmp/np.txt
ogtool accounts password --accounts acc1 --new-password-file /tmp/np.txt \
  --current-password-file /tmp/op.txt --live --allow-write

# 底座 accounts passkey（v1.3.3）：metadata passkey 导出（同页第二任务）
ogtool accounts passkey --accounts acc1 --out-dir data/passkeys --live --allow-write

# 底座 accounts passkey-login（v1.3.4）：batch_login 页的 passkey 分支（读取+构建边界）
ogtool accounts passkey-login --files a.passkey b.passkey
ogtool accounts passkey-login --folder data/passkeys --live --allow-write

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

## live 运行前提（selftest 的 live 检会替你逐条核）

`selftest` 的 live 检不是"有 config 就跑"：它在开跑前把死胡同一条条点名，
每条都带下一步动作。

| 前提 | 缺了时 selftest 说的话 |
|---|---|
| `config.yaml` 存在 | `no config.yaml, skipped - copy config.example.yaml to config.yaml, fill api_id/api_hash and an accounts: entry` |
| 账号池非空（库里有账号） | `the account pool is empty - run \`ogtool accounts add <label>\` (or \`accounts login <label>\`)` |
| 每个账号有 session 文件 | `no session file for <label> - run \`ogtool accounts login <label>\` first` |
| `scripts/live_smoke.py` 在位 | 这条排在账号检之前：脚本缺失按安装损坏报，不被账号空池挡住 |

真跑（`--live --allow-write`）另有两条铁律：

1. **只对自己**：发消息只发自己（`@自己` / Saved Messages）与自建测试夹具；
   自检冒烟的每个写用例都构造在"自己 + 自建群/频道"上。
2. **预算不继承**：冒烟发送用例带 `--daily-limit 0`，保证冒烟通过与否不取决于
   当天前面用例烧掉了多少预算；日常页面的默认 50 条/天不变。

live 用例全部登记在 `scripts/live_smoke.py`（`Case` 表），**全部 25 个都在
套件里**（`in_suite` 皆为 true），每轮六检必跑；外加 `live_cases.yaml` 里的
机器本地用例。听型两页（auto_reply / watch_chat）用 `--run-seconds` 自限时，
到点自己闭账出回执，不需要外部信号。

听型两页的"落行"前提——**要有第二个对话方向该账号发消息**——已由第二账号
真码接入解除：第二账号（label `a`）登录入库后，`puppet1` 冒烟用例用双账号
话术（`patterns/puppet_two.txt`，A1/A2 各一行）在夹具群跑 puppet_chat v1，
断言 `ok: 2`；auto_reply 与 watch_chat 的正向行由第二账号私聊/群发探针
消息真实产生（`t.me/c/<id>` 目标经 `PeerChannel` 形式解析，第二账号无需
先缓存对话即可落行）。每轮 sweep 里的短跑（`--run-seconds 3`）仍以
`DONE:` 闭账为准——落不落行取决于当轮有没有探针消息。

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
  cli.py          argparse 入口，28 个子命令
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

## live 路径体检（真号联调后）

判据只有一条：**有没有真的发生网络副作用**——真的调了 Telethon，并且用上了
它的返回值。「真」= 调了且消费结果；「部分」= 调了但有一半没落地；「假」= stub /
回放 / 只打印。`_AwaitedClient` 只能防「协程没 await」，防不住「根本没调」，
所以这张表按副作用判，不按代码里有没有 `ctx.live` 判。

| feature | live 走向 | 判定 | 证据（函数名:行号） |
|---|---|---|---|
| `add_contact` | `add_contact()` 真调用；请求/操作间隔真等 | 真 | `add_contact.py:157/171` |
| `ai_chat` | AI 生成 → `send_message()`，间隔真等 | 真 | `ai_chat.py:184/186` |
| `ai_clone` | live 回读源频道最近 `max` 条 → 过滤/改写 → 真发 | 真 | `ai_clone.py:241/268` |
| `ai_comment` | `send_message(reply_to=post_id)` 真落在帖子下 | 部分 | `ai_comment.py:266`（点赞/表情未落 reaction） |
| `auto_reply` | `add_event_handler()` + `run_forever()`，命中即回 | 真 | `auto_reply.py:209/214` |
| `auto_script` | 每步真调 sender；`wait` 步真等 | 真 | `auto_script.py:159/172` |
| `batch_invite` | `invite()` + PEER_FLOOD 退避 + 邀请间隔真等 | 真 | `batch_invite.py:231/245` |
| `batch_invite_v2` | 同上，目标取自己联系人 + 邀请间隔真等 | 真 | `batch_invite_v2.py:200/218` |
| `batch_join` | `join()` 真调用，按 job 结果入 rows；请求/单号间隔真等 | 真 | `batch_join.py:156` |
| `batch_login` | 导入是文件操作；`--logout` 真 `log_out()` | 真 | `batch_login.py:216` |
| `batch_rename` | `edit_profile()` 真调用；操作间隔真等 | 真 | `batch_rename.py:154/161` |
| `copy_chat` | live 回读源历史（`capture_minutes` 窗口）→ 过滤/替换/排期 → 真发 | 真 | `copy_chat.py:296/349` |
| `export_accounts` | 只读库与文件，无网络语义（会话归档是复制） | 真 | `export_accounts.py:182` |
| `filter_group` | `get_entity()` 真解析后六分类；全局/单号间隔真等 | 真 | `filter_group.py:148/160` |
| `filter_phone` | `get_entity()` 真解析；号码一律按电话解析 | 真 | `filter_phone.py:145` |
| `keep_online` | `ping` → `is_user_authorized()` + `get_me()` | 真 | `keep_online.py:91` |
| `mass_send` | `send_message()`，message id 入库，间隔真等 | 真 | `mass_send.py:232/234` |
| `mass_send_v2` | `get_contacts()` 取每个号自己的联系人 → 真发 | 真 | `mass_send_v2.py:337` |
| `mix_market` | 五阶段全走 sender（加联系人/建群/炒群/拉人/私信） | 真 | `mix_market.py:347/376/407/515` |
| `proxy` | `--test` 真 TCP connect；`--bind` 真 `set_proxy()` | 真 | `proxy.py:174/195` |
| `puppet_chat` | `send_message()` + 间隔真 sleep | 真 | `puppet_chat.py:443/435` |
| `puppet_chat_v2` | 复用 `play()` 真发，启动偏移已加进 delay；执行器：统计落盘 `puppet_chat_<task>_<cycle>.json`、`--resume` 读回执库、`--listen` 真 handler + `run_forever()` 收音 | 真 | `puppet_chat_v2.py:284` → `puppet_chat.py:443/435`；`puppet_chat_v2.py:297-299/250/374/383` |
| `video_manage` | `join()`/`leave()` 真调用；操作间隔真等 | 真 | `video_manage.py:97/101` |
| `watch_chat` | `add_event_handler()` + `run_forever()`，命中后 `await send_message()` | 真（本轮修） | `watch_chat.py:260/266/284` |
| `translate` | live 读聊天历史（`sender.history()`）→ 真翻译（AI 端点 / Azure REST）→ `translation` 表落缓存；`--auto-received/--auto-sent` 分流收/发消息 | 真 | `translate.py:482/418/258/212`；缓存落 `store.py:721`，批量查询 `store.py:739` |
| `group_manage` | 转让三步全走 sender：新 owner `join()`/`invite()` → `edit_creator()`（`GetPasswordRequest` + `compute_check` + `messages.editChatCreator`）→ 源账号 `leave()`；`--from-store` 读 `group_mgr` 表，转成即落库 | 真 | `group_manage.py:207/219/234`；原语 `senders.py:654/1364`，表 `store.py:909/951` |

**本轮修掉的 7 处假 live**（每处都有能在旧代码上变红的测试）：

1. `watch_chat --live` 只回放 `config.incoming`，不注册 handler、不进循环，
   于是「监听」立刻退出还报 `watched: 1`（那只是目标数）。现在真监听、
   SIGINT 走 `finally` 关成 done，`watched` 改成真正的消息条数。
2. `--targets 6805350975` 被当电话号（`+6805350975`）：Telethon 把任何纯数字
   字符串按电话解析。现在纯数字 5–12 位先当 id（`int`），失败再退回电话形态；
   带 `+` 一律按电话。
3. `batch_login --logout` 只打印 `logout all <label>`，一个号都没退。现在 live 真
   `log_out()` 并把账号置 `logged-out`。
4. `proxy --bind` 丢掉 `set_proxy` 的返回值，失败也打 `bind acc1 -> #1 socks5://…`。
   现在按 `ok/code` 分别打印。
5. `auto_script` 的 `wait` 步在 live 下不睡（只有 dry-run 打「would wait」），整脚本
   零节奏。现在真等。
6. `mass_send` 在 live 下先把整队写成 `planned` 再真发，results 里同一条既有
   `planned` 又有 `sent`。现在 planned 行只在 dry-run 写。
7. `batch_join` 跑完把每个 job 一律记成成功（`record(..., True, "planned")`），
   失败的 join 也写成 `sent`。现在按 `execute()` 里的真实结果入 rows。
   顺带：`ai_comment` 的评论原来发到频道本身而不是帖子下，现在带 `reply_to`。

**这一轮（P0–P2）修的**（每处都有能在旧代码上变红的测试，测试名见报告）：

1. **六个页面的间隔在 live 下不 sleep**：`add_contact` / `batch_rename` /
   `batch_join` / `batch_invite` / `batch_invite_v2` / `video_manage`，间隔只进
   ETA，真号连发等于没有节流。现在全部走 `_common.pause()`，只在 live 睡、
   dry-run 秒回；`filter_group` 的 `--global-interval-ms` / `--account-interval-ms`
   同样落地（这两个参数只有它有）。
2. **`copy_chat` / `ai_clone` 在 live 下不读源历史**：新增
   `senders.history(account, target, minutes, limit)`（`iter_messages` +
   `offset_date`），回读结果按 `peer|sender|text` 喂 `sources.parse_many`，
   和任务文件走同一条解析。采集失败按错误码报出来，不再静默成空。
3. **live 收尾不 `disconnect()`**：`accounts test --live` 在「`get_me()` 空」
   和抛异常两条分支上都会漏掉 `disconnect()`，留下 pending task（退出时那屏
   `Task was destroyed but it is pending!`）。现在 `finally` 里统一关，且
   `TelethonSender.close()` 有测试守着（两个 client 各断一次、可重复调用）。
4. **`--threads` 名字有误导**：它只是计划期并发度（决定账号交叉粒度），不是
   线程池。旗标名保留（跟参考端一致），帮助文字改成
   `planning concurrency only (requests are serial)`，README 见缺口第 3 条。

**仍未做的** 见「已知缺口」。

## 已知缺口

1. **`ai_comment` 的点赞与表情只计数不落地**：Telethon 1.45 没有
   `send_reaction()`，要裸 `functions.messages.SendReactionRequest`；而且一个账号
   对一条帖子只能点一次，所以「每帖点赞数」实际被账号池大小卡住。（本轮登记，未做）
2. **`batch_login` 的三种批量退出模式指向同一个账号池**：冻结号 / 所有号 /
   双向号在 `accounts` 表里没有区分字段，`--logout` 目前就是「这批号全退」。
   （本轮登记，未做）
3. **`--threads` 是计划期并发度**（决定账号交叉粒度），不是真线程池：所有请求
   在一个进程里串行发出，页面的「线程数」只影响排期。
4. **`batch_login` 不读 `tdata`**：只处理 `.session` + sidecar `*.json`，二进制
   tdata 解析不在范围内。
5. **AI 的真实 endpoint 没跑过**：无 key 降级、假 transport（正常/超时/报错）
   三条分支都有单测，但没有用真 key 打过 `chat/completions`。
6. **`proxy --test` 在 `--live` 下只做 TCP 连通性探测**（socket connect），
   不是真的走代理打 MTProto。
7. **贴纸的 `unknown/setId/...` 两种写法在 `--live` 下不解析**：只有能落到本地
   文件的引用会真的发；unknown 形态 dry-run 照常打印，live 退化成发正文。
8. **#0/#19 的 `A<n>` 槽位重绑只在日限额触发时发生**，不按真实在线状态重排。
9. **live 回读历史只取正文**：`senders.history()` 跳过纯媒体帖（`count` 是扫描
   条数，可能大于返回条数），也不下载媒体——搬运带图的帖子目前会丢图；要保留
   媒体得先下载到本地再走 `Content.media`。
10. **历史回读的上限是写死的**：`copy_chat` 每个源最多 200 条
    （`CAPTURE_LIMIT`），`ai_clone` 用页面自己的 `max_items`。参考端没有单独的
    「条数」字段，所以没有做成旗标。

## 未接通（不做）

下面这些功能**对外发起的批量路径在 `--live` 下是空的**：它们会跑完计划、
打印进度，然后每一条都落成 `UNKNOWN`（failed 计数加 1，退出码非 0）。

原因是 `ogtool/senders.py` 里有 6 个方法调在 `TelegramClient` 上根本不存在
（Telethon 1.45 实测 `hasattr()` 为假）：

| 被调用的名字 | 真实 API |
|---|---|
| `client.join` | `functions.channels.JoinChannelRequest` / `functions.messages.ImportChatInviteRequest` |
| `client.leave` | `functions.channels.LeaveChannelRequest` / `functions.messages.DeleteChatRequest` |
| `client.invite` | `functions.channels.InviteToChannelRequest` |
| `client.add_contact` | `functions.contacts.AddContactRequest` |
| `client.edit_profile` | `functions.account.UpdateProfileRequest` |
| `client.get_contacts` | `functions.contacts.GetContactsRequest` |

**本轮不修，是明确的不做，不是「计划中」。** 受影响的命令：

`batch_join`、`batch_invite`、`batch_invite_v2`、`mass_send_v2`（联系人池）、
`mix_market`（加人 + 邀请两个阶段）、`add_contact`、`batch_rename`（改名走
`edit_profile`）、`auto_script`（`join`/`leave`/`invite`/`contact`/`profile` 五个
动作）、`puppet_chat` / `puppet_chat_v2` / `ai_chat` / `ai_comment`（只影响需要
先加入目标群的那条路径，直接发消息不受影响）。

**已经确认能用**（真号实测）：`send` 家族（`send_message` / `send_file` /
`forward_messages`）、`resolve`、`get_participants`、`get_entity`、`history`、
`ping`、`proxy`、`log_out`、`create_group`，以及本轮的 `filter_group` 链接形态
——后者走只读的 `CheckChatInviteRequest`，不加入、不发消息。

这个缺口现在有测试守着，`tests/test_client_surface.py`：
- `test_every_method_the_sender_calls_exists_on_the_real_client` 把
  `senders.py` 里每个 `client.<name>` 抽出来跟 `TelegramClient` 对一遍；
- `KNOWN_MISSING` 是上面这 6 条的白名单，**双向校验**：多出一个新名字即红，
  修好一条后忘了删白名单同样即红；
- `conftest.py` 的 `spec_client` fixture 是 `MagicMock(spec=TelegramClient)`，
  新测试拿它当替身，生造方法当场 `AttributeError`，不会再静默通过。

在 `3bd22e7` 上用空白名单跑这条测试是红的，报的就是上面 6 个名字。
