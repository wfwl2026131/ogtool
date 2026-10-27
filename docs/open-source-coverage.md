# 25 项功能 × 开源对照

> 结论先行:**单点功能开源圈都有,整台控制台没有**。分三类处理——
> **可借逻辑**(看别人的实现,抄思路不抄代码)、**已有轮子**(直接用库,
> 自己手写是坑)、**自建护城河**(全网没有,这是 ogtool 的价值所在)。

| # | 功能 | 开源参照(星数为 2026-10 查证) | 类别 | 落法 |
|---|------|------------------------------|------|------|
| 0 | 🤖 话术炒群 puppet_chat | 英文圈无;中文圈全是闭源付费(Fzdn1 类) | **自建护城河** | 分角色话术+延迟/回复层级+AI 润色是本项目独有 |
| 1 | 📋 复制炒群 copy_chat | Ultroid 插件制(Telethon)⭐2983 | 可借逻辑 | 转发/复制去重的插件写法参考 `pyUltroid/fns` |
| 2 | 🧠 AI原创炒群 ai_chat | 无专门开源;chigwell/telegram-mcp ⭐1908 的接口切分可参考 | 可借逻辑 | LLM 轮询+模板回退已是更完整形态 |
| 3 | 💬 AI智能评论 ai_comment | 无 | 自建护城河 | — |
| 4 | 📦 AI搬运频道 ai_clone | mehdisayyadnahed/telegram-auto-forwarder 等 ⭐7 玩具级 | 自建护城河 | AI 改写+风格映射无开源对标 |
| 5 | 📢 群发消息 mass_send | TelegramMassDMBot ⭐72、saifalisew1508 ⭐27 | 可借逻辑 | 多账号轮发+FloodWait 处理模式已对齐;每日限额/退避比它们全 |
| 6 | 🔄 自动回复 auto_reply | Ultroid/PagerMaid-Pyro ⭐813/Hikka ⭐378 | 已有轮子 | 规则引擎各家长项,ogtool 加了收据与重复抑制 |
| 7 | 👀 监听聊天 watch_chat | 同上 | 已有轮子 | relay 语义自建,收据链独有 |
| 8 | 👥 批量加群 batch_join | ZAID-USERBOT multi-client ⭐220 | 可借逻辑 | 请求间隔/账号间隔/上限三闸比开源全 |
| 9 | 🛅 批量上号 batch_login | **opentele2**(DedInc fork,Rust 核)⭐47 | **已有轮子** | tdata↔session 互转用它,不手写二进制格式;见下文 |
| 10 | 🔠 批量改名 batch_rename | 无专门项目 | 自建护城河 | 暂停/继续/停止状态机无对标 |
| 11 | 🔂 批量拉人 batch_invite | AbirHasan2005/TelegramScraper ⭐716(采集+邀请+隐藏成员) | 可借逻辑 | 采集逻辑可对照;邀请策略(strategy/导出)自建 |
| 12 | ⚖️ 筛选手机 filter_phone | 无 | 自建护城河 | — |
| 13 | 🧮 筛选群组 filter_group | TelegramScraper 的采集半边 | 可借逻辑 | deep 模式自建 |
| 14 | 🎥 视频管理 video_manage | 无 | 自建护城河 | — |
| 15 | 📶 代理配置 proxy | 玩具脚本级 | 自建护城河 | 绑定账号+测试+假行清理链独有 |
| 16 | ➕ 批量联系人 add_contact | TelegramMassDMBot 半边 | 可借逻辑 | 限速闸自建 |
| 17 | keep_online | 只有玩具脚本 | 自建护城河 | 逐账号落行+收据独有 |
| 18 | export_accounts | 无 | 自建护城河 | — |
| 19 | auto_script | 无 | 自建护城河 | — |
| 20 | settings(参数页) | 无 | 自建护城河 | — |
| 21 | accounts(池) | ZAID multi-client | 可借逻辑 | tdata 导入后对齐 opentele2 |
| 22 | proxy 动态段 | 无 | 自建护城河 | — |

## 跨功能的真护城河(全网 userbot 都没有)

开源 userbot 全是"脚本能跑就行",没有一家有:

- **回执台账**:`ok + failed == planned` 不变量,监听页 `plan_rows` 回填
- **证据链**:对照表证据段由 `sync_evidence.py` 生成,`docs` 检查比对 pytest 收集数
- **selftest 六检 + sweep 双跑一致**:行为回归是检查项,不是口头承诺
- **写闸门**:`--allow-write` 默认拒绝,闸门两向(拒/放)都有进程级测试
- **默认 dry-run**:完整跑流程只 stub 网络层,`PLAN:` 行 + 退出码约定

## batch_login 的 tdata 路线(#9)

Telegram Desktop 的 `tdata` 目录是二进制格式(本地密钥 + AES 加密),
手写解析是深坑。可选依赖 **opentele2** 一行转换:

```python
from opentele2.td import TDesktop
tdesk = TDesktop("tdata-dir")           # 读 tdata
client = tdesk.ToTelethon("out.session", UseCurrentSession)  # 转出 Telethon 会话
```

- 装了 `opentele2`(`pip install opentele2`):`batch_login --import-tdata <目录>` 真转,逐账号落行
- 没装:明确的 `pip install opentele2` 提示,退出码 2,不静默降级
- 现有 `.session`/zip 路线不动,两者可并存(见 `--import-folder/--import-zip`)
