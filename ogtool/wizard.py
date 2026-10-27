#!/usr/bin/env python3
"""ogtool 交互式向导 —— 问答式跑常用功能，不用背旗子。

用法:
    ~/.venvs/ogtool/bin/python scripts/wizard.py
或直接:
    ~/.venvs/ogtool/bin/ogtool-wizard   (安装 wheel 后)

每一轮都是: 挑功能 → 问几个问题 → 先 dry-run 给你看计划 → 你确认了
才真跑(--live,写操作再要一次 --allow-write)。答案只在内存里,
不会写进 config.yaml。
"""

from __future__ import annotations

import json
import subprocess
import sys

PY = sys.executable

# (key, 一句话) —— 和 quickstart 场景一一对应
MENU = [
    ("mass_send", "📢 群发消息(一组目标发一条文案)"),
    ("watch_chat", "👀 盯群转发(源群消息转进目标群)"),
    ("auto_reply", "🔄 私聊自动回复(关键词=>话术)"),
    ("puppet_chat", "🤖 话术炒群(按剧本轮流发言)"),
    ("batch_invite", "👥 批量拉人(把人拉进指定群)"),
    ("batch_join", "🚪 批量加群(一组账号加入一组群)"),
    ("video_manage", "🎥 进群语音房(join/speak)"),
    ("test", "🩺 只做连通性测试(accounts test)"),
]


def ask(prompt: str, default: str = "") -> str:
    tail = f" [{default}]" if default else ""
    value = input(f"{prompt}{tail}: ").strip()
    return value or default


def ask_int(prompt: str, default: int) -> int:
    raw = ask(prompt, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def pick_accounts() -> str:
    print("\n先看一眼账号池:")
    subprocess.run([PY, "-m", "ogtool", "accounts", "list"])
    return ask("用哪些账号(逗号分隔,如 t1 或 t1,a)")


def show_plan(argv: list[str]) -> bool:
    """dry-run 出计划,返回用户要不要继续。"""
    print("\n—— 先跑 dry-run(不出网)——\n")
    plan = subprocess.run([PY, "-m", "ogtool", *argv, "--dry-run"])
    print()
    if plan.returncode != 0:
        print("dry-run 退出码非 0,上面应有报错,先解决再试。")
        return False
    answer = input("计划如上。真跑吗?输入 yes 才执行(其他任何输入=取消): ")
    return answer.strip().lower() == "yes"


def run_live(argv: list[str], *, write: bool) -> None:
    live = [*argv, "--live"]
    if write:
        confirm = input("这是写操作(真发/真拉/真改名)。输 yes 确认加 --allow-write: ")
        if confirm.strip().lower() != "yes":
            print("取消。")
            return
        live.append("--allow-write")
    print()
    done = subprocess.run([PY, "-m", "ogtool", *live])
    if done.returncode == 0:
        print("\n✅ 跑完。回执在 data/logs/receipts/ 下,最新一张:")
        # 找回执是锦上添花: 没 config.yaml(冷环境/CI)就闭嘴,不砸向导。
        try:
            from ogtool.config import Settings
            from ogtool.receipts import receipt_dir

            receipts = sorted(receipt_dir(
                Settings.load("config.yaml")).glob("*.json"),
                key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:  # noqa: BLE001 - the run already succeeded
            return
        if receipts:
            body = json.loads(receipts[0].read_text())
            print(f"   {receipts[0].name}  planned={body.get('planned')} "
                  f"ok={body.get('ok')} failed={body.get('failed')}")
    else:
        print(f"\n⚠️ 退出码 {done.returncode}。看上面输出;回执里 codes 字段"
              "有失败码直方图。")


def flow_mass_send() -> None:
    accounts = pick_accounts()
    targets = ask("目标群(逗号分隔链接/@用户名)")
    text = ask("发送的文案")
    imin = ask_int("账号间隔最小秒数", 30)
    imax = ask_int("账号间隔最大秒数", 90)
    argv = ["mass_send", "--accounts", accounts, "--targets", targets,
            "--text", text, "--interval-min", str(imin),
            "--interval-max", str(imax)]
    if show_plan(argv):
        run_live(argv, write=True)


def flow_watch_chat() -> None:
    accounts = pick_accounts()
    targets = ask("监听的群(源,逗号分隔)")
    relay = ask("转发到哪个群(留空=只监听不转发)")
    seconds = ask_int("监听多少秒后自动结束", 600)
    argv = ["watch_chat", "--accounts", accounts, "--targets", targets,
            "--run-seconds", str(seconds)]
    if relay:
        argv += ["--relay", relay]
    if show_plan(argv):
        run_live(argv, write=bool(relay))


def flow_auto_reply() -> None:
    accounts = pick_accounts()
    rules = ask("规则文件路径(如 rules.yaml)")
    seconds = ask_int("监听多少秒后自动结束(0=一直跑到 Ctrl+C)", 0)
    argv = ["auto_reply", "--accounts", accounts, "--rules", rules]
    if seconds:
        argv += ["--run-seconds", str(seconds)]
    if show_plan(argv):
        run_live(argv, write=True)


def flow_puppet_chat() -> None:
    accounts = pick_accounts()
    group = ask("在哪个群发言")
    script = ask("剧本文件(如 script.yaml)")
    argv = ["puppet_chat", "--accounts", accounts, "--group", group,
            "--script", script]
    if show_plan(argv):
        run_live(argv, write=True)


def flow_batch_invite() -> None:
    accounts = pick_accounts()
    groups = ask("拉进哪个群(逗号分隔)")
    members = ask("要拉的人(手机号或用户名,逗号分隔)")
    argv = ["batch_invite", "--accounts", accounts, "--groups", groups,
            "--members", members]
    if show_plan(argv):
        run_live(argv, write=True)


def flow_batch_join() -> None:
    accounts = pick_accounts()
    targets = ask("要加入的群(逗号分隔)")
    argv = ["batch_join", "--accounts", accounts, "--targets", targets]
    if show_plan(argv):
        run_live(argv, write=True)


def flow_video_manage() -> None:
    accounts = pick_accounts()
    link = ask("群链接(群里有语音房才进得去)")
    action = ask("动作 join/speak/leave", "speak")
    argv = ["video_manage", "--accounts", accounts, "--link", link,
            "--action", action]
    if show_plan(argv):
        run_live(argv, write=True)


def flow_test() -> None:
    accounts = pick_accounts()
    run_live(["accounts", "test", "--accounts", accounts], write=False)


FLOWS = {
    "mass_send": flow_mass_send,
    "watch_chat": flow_watch_chat,
    "auto_reply": flow_auto_reply,
    "puppet_chat": flow_puppet_chat,
    "batch_invite": flow_batch_invite,
    "batch_join": flow_batch_join,
    "video_manage": flow_video_manage,
    "test": flow_test,
}


def main() -> int:
    print("=" * 56)
    print("ogtool 交互式向导 —— 每一步先 dry-run,确认才真跑")
    print("=" * 56)
    while True:
        print()
        for index, (_key, label) in enumerate(MENU, start=1):
            print(f"  {index}. {label}")
        print("  q. 退出")
        choice = ask("\n选一个", "q").strip().lower()
        if choice in ("q", "quit", "exit", ""):
            print("再见。")
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(MENU):
            print("没这个选项。")
            continue
        key = MENU[int(choice) - 1][0]
        try:
            FLOWS[key]()
        except KeyboardInterrupt:
            print("\n中断,回主菜单。")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n再见。")
        sys.exit(130)
