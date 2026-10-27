# 参与开发 ogtool

仓库不带任何账号、会话或私群链接——`config.yaml`、`data/`、`*.session`
都在 `.gitignore` 里,提交前确认你的改动也没有把它们带进来
(`git grep 66661106045 14323066662` 这类自查,或直接 `git diff --staged` 过一眼)。

## 环境与底线

```bash
python3 -m venv ~/.venvs/ogtool
~/.venvs/ogtool/bin/pip install -e ".[dev]" "telethon>=1.36"
~/.venvs/ogtool/bin/python -m pytest -o addopts="" -q   # 必须 1043+ 全绿
~/.venvs/ogtool/bin/python -m ruff check .              # 必须零告警
```

PR 上 CI 会跑同样的两步(3.11/3.12/3.13)。本地先过再推,别让 CI 当你的
第一次编译。

## 四条纪律(改代码前必读)

1. **写闸门**:一切向 Telegram 发送内容的页面默认拒绝,必须显式
   `--allow-write` 才放行。新写页面进 `WRITE_FEATURES`,并且
   `scripts/live_smoke.py` 的 `WRITE_CASES` 同步登记——selftest 的
   `gate` 检查会真跑一遍"无旗标必须拒、有旗标必须通"两个方向,漏一边
   就是红的。
2. **回执不变量**:每个任务的收据里 `ok + failed == planned`。凡是"行数
   到 close 时才确定"的监听型页面,close 时传 `plan_rows=True` 让
   `close_task` 用实际行数回填 `planned`——不要自己绕过收据层写文件。
   selftest 的 `receipts` 检查扫全量收据,`unaccounted != 0` 即失败。
3. **默认 dry-run**:所有功能默认 `--dry-run`,dry-run 是"完整跑一遍流程、
   只把真正调 Telethon 那一步 stub 掉",不是跳过。新功能的 dry-run 必须
   打出 `PLAN:` 行并退出码 0。
4. **证据链**:改了行为就跑 `scripts/sync_evidence.py` 重写对照表的证据
   段(`<!-- evidence:start/end -->` 之间),selftest 的 `docs` 检查会
   比对证据块里的 pytest 数字和当前收集数,过期的证据块 = 红的。

## live smoke 只在本机跑

`scripts/live_smoke.py` 会对真账号发真实消息,**不在 CI 里跑**,仓库也
不携带它的目标:夹具群链接放在 gitignored 的 `data/fixture_group`(一行),
没有这个文件时夹具用例自动跳过并说明原因。CI 里的 `--only` 探针用例
(写闸门自检)走 `config.example.yaml` 的占位配置,不碰网络。

贡献者不需要跑 live smoke;selftest 六检里和真机相关的检查会在没有
`config.yaml` 时给出明确的跳过/占位行为,而不是误报。

## 提交什么

- 行为改动必须带测试(新页面 = 新测试文件,闸门两个方向都要有)。
- 提交信息说"为什么",不说"改了哪些文件"——diff 自己会说话。
- 每个 PR 一件事。顺手的重构单独开 PR。
