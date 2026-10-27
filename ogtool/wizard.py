#!/usr/bin/env python3
"""ogtool 交互式向导 —— 问答式跑全部 26 个子命令，不用背旗子。

用法:
    ogtool-wizard        (安装后; 等价 python -m ogtool.wizard)

规矩不变: 挑功能 → 一条条问 → 先 dry-run 给你看计划 → 输 yes 才真跑
(--live),写操作再要一次 yes 才带 --allow-write。菜单按「只读 / ⚠写入」
分组,写闸门对应 ogtool.features._common.WRITE_FEATURES。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PY = sys.executable

# ---------------------------------------------------------------- 定制问答 --
# 这 8 个高频功能手写问答(问题更贴心);其余功能走 GENERIC 引擎。

MENU_CUSTOM = [
    ("mass_send", "📢 群发消息(一组目标发一条文案)"),
    ("watch_chat", "👀 盯群转发(源群消息转进目标群)"),
    ("auto_reply", "🔄 私聊自动回复(关键词=>话术)"),
    ("puppet_chat", "🤖 话术炒群(按剧本轮流发言)"),
    ("batch_invite", "👥 批量拉人(把人拉进指定群)"),
    ("batch_join", "🚪 批量加群(一组账号加入一组群)"),
    ("video_manage", "🎥 进群语音房(join/speak)"),
    ("test", "🩺 账号连通性测试(accounts test)"),
]

MENU_CUSTOM_KEYS = {key for key, _label in MENU_CUSTOM}

# -------------------------------------------------------------- 通用问答 ----
# (旗子, 问题, 默认, 类型)。kind: str | int | flag(答 y 就带旗)。
# 第一个 --accounts 一律先展示账号池再问。

GENERIC: dict[str, dict] = {
    # ---- ⚠ 写入组 ----
    "ai_chat": {
        "label": "🧠 AI原创炒群(生成话术发进目标群)",
        "steps": [
            ("--targets", "目标群(逗号分隔)", "", "str"),
            ("--count", "每个群发几条", "1", "int"),
            ("--interval-min", "账号间隔最小秒", "30", "int"),
            ("--interval-max", "账号间隔最大秒", "90", "int"),
        ],
    },
    "ai_comment": {
        "label": "💬 AI智能评论(评论目标群最近的帖子)",
        "steps": [
            ("--targets", "目标群(逗号分隔)", "", "str"),
            ("--posts", "每个群评论几条帖", "1", "int"),
        ],
    },
    "ai_clone": {
        "label": "📦 AI搬运频道(源频道搬运到目标频道)",
        "steps": [
            ("--sources", "源频道(逗号分隔)", "", "str"),
            ("--targets", "目标频道(逗号分隔)", "", "str"),
            ("--max-items", "最多搬几条", "5", "int"),
            ("--mode", "模式 copy/polish", "copy", "str"),
        ],
    },
    "copy_chat": {
        "label": "📋 复制炒群(源群内容复制进目标群)",
        "steps": [
            ("--sources", "源群(逗号分隔)", "", "str"),
            ("--targets", "目标群(逗号分隔)", "", "str"),
            ("--copy-mode", "模式 copy/polish", "copy", "str"),
        ],
    },
    "batch_rename": {
        "label": "🔠 批量改名(按清单改名/头像/简介)",
        "steps": [
            ("--file", "改名清单文件(如 names.txt)", "", "str"),
        ],
    },
    "add_contact": {
        "label": "➕ 批量加联系人",
        "steps": [
            ("--contacts", "要加的人(手机号/@用户名,逗号分隔)", "", "str"),
        ],
    },
    "mass_send_v2": {
        "label": "📨 群发私信v2(多账号轮发,更细的错峰)",
        "steps": [
            ("--targets", "目标(群链接/@用户名/手机号,逗号分隔)", "", "str"),
            ("--text", "发送的文案", "", "str"),
        ],
    },
    "puppet_chat_v2": {
        "label": "🤖 话术炒群v2(多账号分批进群,错峰启动)",
        "steps": [
            ("--targets", "目标群(逗号分隔)", "", "str"),
            ("--pattern", "剧本文件(如 pattern.txt)", "", "str"),
            ("--startup-interval", "账号启动间隔秒", "20", "int"),
        ],
    },
    "batch_invite_v2": {
        "label": "🧲 批量拉人v2(多账号分担,带成功上限)",
        "steps": [
            ("--targets", "拉进哪个群(逗号分隔)", "", "str"),
            ("--contacts", "要拉的人(手机号/@用户名,逗号分隔)", "", "str"),
        ],
    },
    "mix_market": {
        "label": "🧩 自动营销(进群→发广告→加联系→查活)",
        "steps": [
            ("--targets", "目标群(逗号分隔)", "", "str"),
            ("--pattern", "剧本文件", "", "str"),
            ("--contact", "要留的联系方式", "", "str"),
            ("--stop-after", "跑几轮停", "10", "int"),
        ],
    },
    "auto_script": {
        "label": "📜 自动脚本(跑自定义步骤脚本)",
        "steps": [
            ("--script", "脚本名(先选 0 可列全部)", "", "str"),
        ],
    },
    # ---- 只读组 ----
    "keep_online": {
        "label": "🟢 保持在线(挂机心跳)",
        "steps": [
            ("--interval", "心跳间隔秒", "60", "int"),
            ("--loops", "跳几次", "1", "int"),
        ],
    },
    "export_accounts": {
        "label": "📤 导出账号与会话",
        "steps": [
            ("--output-dir", "导出目录(留空=默认 data/export)", "", "str"),
            ("--format", "格式 yaml/json", "yaml", "str"),
        ],
    },
    "filter_phone": {
        "label": "⚖️ 筛选手机号(看哪些号注册了 Telegram)",
        "steps": [
            ("--numbers", "手机号(逗号分隔)", "", "str"),
        ],
    },
    "filter_group": {
        "label": "🧮 筛选群组(群规模/活跃度画像)",
        "steps": [
            ("--groups", "群链接(逗号分隔)", "", "str"),
            ("--deep", "深度画像", "", "flag"),
        ],
    },
    "proxy": {
        "label": "📶 代理配置(添加/测试)",
        "steps": [
            ("--add", "添加代理 host:port:user:pass(留空跳过)", "", "str"),
            ("--test", "测一遍所有代理", "", "flag"),
        ],
    },
    "settings": {
        "label": "⚙️ 参数页(看当前全部旋钮)",
        "steps": [
            ("--json", "以 JSON 输出", "y", "flag"),
        ],
    },
    "batch_login": {
        "label": "🛅 批量上号(本地导入: 文件夹/zip/tdata)",
        "steps": [
            ("--import-folder", "session 文件夹路径(留空看下两项)", "", "str"),
            ("--import-zip", "session zip 包路径(留空跳过)", "", "str"),
            ("--import-tdata", "Telegram Desktop tdata 路径(留空跳过)", "",
             "str"),
        ],
    },
}


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


def newest_receipt() -> None:
    """锦上添花: 指出最新回执;冷环境没 config.yaml 就闭嘴。"""
    try:
        from ogtool.config import Settings
        from ogtool.receipts import receipt_dir

        receipts = sorted(receipt_dir(Settings.load("config.yaml"))
                          .glob("*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:  # noqa: BLE001 - the run already succeeded
        return
    if receipts:
        body = json.loads(receipts[0].read_text())
        print(f"   {receipts[0].name}  planned={body.get('planned')} "
              f"ok={body.get('ok')} failed={body.get('failed')}")


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
        newest_receipt()
    else:
        print(f"\n⚠️ 退出码 {done.returncode}。看上面输出;回执里 codes 字段"
              "有失败码直方图。")


# 这些页面吃 --accounts;其余(筛号/筛群、settings、proxy、batch_login、
# export_accounts)操作对象不是账号池,问了反而误导。
ACCOUNTED = frozenset({
    "ai_chat", "ai_comment", "ai_clone", "copy_chat", "batch_rename",
    "add_contact", "mass_send_v2", "puppet_chat_v2", "batch_invite_v2",
    "mix_market", "auto_script", "keep_online",
})


def flow_generic(feature: str) -> None:
    spec = GENERIC[feature]
    argv = [feature]
    if feature in ACCOUNTED:
        accounts = pick_accounts()
        if accounts:
            argv += ["--accounts", accounts]
    for flag, question, default, kind in spec["steps"]:
        if kind == "flag":
            if ask(f"{question}? (y/N)", "N").lower() in ("y", "yes"):
                argv.append(flag)
            continue
        if kind == "int":
            raw = ask(f"{question}", default)
            try:
                argv += [flag, str(int(raw))]
            except ValueError:
                continue
            continue
        value = ask(question, default)
        if value:
            argv += [flag, value]
        elif feature == "auto_script" and flag == "--script":
            argv.append("--list")
    if show_plan(argv):
        run_live(argv, write=feature in WRITE)


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


def flow_for(key: str) -> None:
    if key in FLOWS:
        FLOWS[key]()
    else:
        flow_generic(key)


def menu_order() -> list[str]:
    """Custom flows first, then the generic write ones, then the read ones."""
    return [k for k, _ in MENU_CUSTOM] \
        + [k for k in GENERIC if k in WRITE] \
        + [k for k in GENERIC if k not in WRITE]


def run_menu() -> int:
    print("=" * 56)
    print("ogtool 交互式向导 —— 每一步先 dry-run,确认才真跑")
    print("=" * 56)
    order = menu_order()
    while True:
        print()
        print("—— ⚠ 写入组(真发/真拉/真改名,过双重确认)——")
        for index, key in enumerate(order, start=1):
            if key in WRITE:
                print(f"  {index}. ⚠ {label_of(key)}")
        print("—— 只读组(不碰别人的聊天)——")
        for index, key in enumerate(order, start=1):
            if key not in WRITE:
                print(f"  {index}. {label_of(key)}")
        print("  q. 退出")
        choice = ask("\n选一个", "q").strip().lower()
        if choice in ("q", "quit", "exit", ""):
            print("再见。")
            return 0
        number = int(choice) if choice.isdigit() else -1
        if not 1 <= number <= len(order):
            print("没这个选项。")
            continue
        try:
            flow_for(order[number - 1])
        except KeyboardInterrupt:
            print("\n中断,回主菜单。")


def label_of(key: str) -> str:
    for known, label in MENU_CUSTOM:
        if known == key:
            return label
    return GENERIC[key]["label"]


WRITE = set()  # filled by main() from the registry; tests may preset it


def locate_workdir() -> None:
    """In-place: run where config.yaml lives.

    The wizard drives ``python -m ogtool`` in the CURRENT directory, so a
    bare ``ogtool-wizard`` from $HOME must land on the project folder.  The
    repo checkout is the fallback that matches this machine's layout.
    """
    if Path("config.yaml").is_file():
        return
    home = Path.home() / "tg-src" / "og-scripts"
    if (home / "config.yaml").is_file():
        import os

        os.chdir(home)


def main() -> int:
    global WRITE
    try:
        from ogtool.features import WRITE_FEATURES
        WRITE = set(WRITE_FEATURES)
    except ImportError:  # pragma: no cover - standalone script use
        pass
    locate_workdir()
    return run_menu()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n再见。")
        sys.exit(130)
