## 这件事是什么

<!-- 一句话:修了什么/加了什么。 -->

## 动了哪条纪律

<!-- 全都无关就删掉本节 -->
- [ ] 写闸门(新写页面已进 WRITE_FEATURES + WRITE_CASES,闸门两向测试已加)
- [ ] 回执不变量(ok + failed == planned;监听页用 plan_rows 回填)
- [ ] 默认 dry-run(新功能 dry-run 打 PLAN: 行、退出码 0)
- [ ] 证据链(sync_evidence.py 已重跑,证据块数字与 pytest 一致)

## 自测

<!-- 贴命令输出,不要只说"已通过" -->
- [ ] `python -m pytest -o addopts="" -q` → N passed
- [ ] `ruff check .` → All checks passed
- [ ] 确认 diff 里没有 config.yaml / *.session / data/ / 真实手机号
