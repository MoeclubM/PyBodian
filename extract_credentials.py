#!/usr/bin/env python3
"""
波点音乐凭证提取工具
从波点PC客户端日志中自动提取最新的 UID + Token

CTF Competition - Authorized Security Research

用法:
  python extract_credentials.py          # 提取并显示
  python extract_credentials.py --save   # 提取并保存到 .bodian/auth.json
"""

import os
import sys
import json
import glob
import re

from bodian_toolkit import AUTH_FILE

if sys.platform == "win32":
    os.system("chcp 65001 >nul 2>&1")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─── 路径 ─────────────────────────────────────────────────────────

LOCAL_DATA = os.path.join(os.environ.get("LOCALAPPDATA", ""), "cn.wenyu.bodian", "bodian_pc")
ROAMING_DATA = os.path.join(os.environ.get("APPDATA", ""), "cn.wenyu.bodian", "bodian_pc")
LOG_DIR = os.path.join(LOCAL_DATA, "bdlog")
SHARED_PREFS = os.path.join(ROAMING_DATA, "shared_preferences.json")

# ─── 从日志提取 ───────────────────────────────────────────────────

def extract_from_logs():
    """从波点客户端日志中提取最新的主账号凭证与 QQ 音乐授权"""
    if not os.path.isdir(LOG_DIR):
        print(f"  日志目录不存在: {LOG_DIR}")
        return None

    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "log_*.log")), key=os.path.getmtime, reverse=True)
    if not log_files:
        print("  未找到日志文件")
        return None

    latest = None
    latest_time = ""
    latest_url_uid = ""
    latest_url_token = ""
    latest_url_time = ""
    latest_url_source = ""

    for log_file in log_files:
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    time_m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{3})?)", line)
                    timestamp = time_m.group(1) if time_m else ""
                    url_m = re.search(r"uid=(\d+)&token=([0-9a-f]{32})", line)
                    if url_m and timestamp >= latest_url_time:
                        latest_url_uid, latest_url_token = url_m.groups()
                        latest_url_time = timestamp
                        latest_url_source = os.path.basename(log_file)
                    if '"code":200' not in line or '"userInfo"' not in line or '"token":"' not in line:
                        continue
                    json_start = line.find('{"code":200')
                    if json_start < 0:
                        continue
                    try:
                        payload = json.loads(line[json_start:])
                    except Exception:
                        continue
                    data = payload.get("data") or {}
                    user_info = data.get("userInfo") or {}
                    if not data.get("id") or not data.get("token"):
                        continue

                    if timestamp < latest_time:
                        continue

                    qq_auth = data.get("qqMusicAuth") or {}
                    latest_time = timestamp
                    latest = {
                        "uid": str(data.get("id")),
                        "token": data.get("token", ""),
                        "nickname": user_info.get("nickname", ""),
                        "auth_type": user_info.get("authType", 0),
                        "user_is_vip": user_info.get("isVip", 0) or 0,
                        "vip_type": (data.get("payInfo") or {}).get("vipType", user_info.get("vipType", 0)) or 0,
                        "pay_vip_type": (data.get("payInfo") or {}).get("payVipType", user_info.get("payVipType", 0)) or 0,
                        "act_vip_type": (data.get("payInfo") or {}).get("actVipType", 0) or 0,
                        "pay_expire_date": (data.get("payInfo") or {}).get("payExpireDate", (data.get("payInfo") or {}).get("expireDate", 0)) or 0,
                        "act_expire_date": (data.get("payInfo") or {}).get("actExpireDate", 0) or 0,
                        "pay_is_vip_boolean": bool((data.get("payInfo") or {}).get("isVipBoolean", False)),
                        "log_time": timestamp,
                        "source": os.path.basename(log_file),
                        "qq_open_id": str(qq_auth.get("openId", "") or ""),
                        "qq_open_token": qq_auth.get("openToken", "") or "",
                        "qq_expire_time": qq_auth.get("expireTime", 0) or 0,
                        "qq_nickname": qq_auth.get("nickname", "") or "",
                        "qq_head_img": qq_auth.get("headImg", "") or "",
                        "qq_import_status": qq_auth.get("importStatus", 0) or 0,
                    }
        except Exception as e:
            print(f"  读取 {log_file} 失败: {e}")
            continue

    if latest_url_uid and latest_url_token and latest_url_time >= latest_time:
        if latest is None:
            latest = {
                "uid": latest_url_uid,
                "token": latest_url_token,
                "nickname": "",
                "auth_type": 0,
                "log_time": latest_url_time,
                "source": latest_url_source,
                "qq_open_id": "",
                "qq_open_token": "",
                "qq_expire_time": 0,
                "qq_nickname": "",
                "qq_head_img": "",
                "qq_import_status": 0,
            }
        else:
            latest["uid"] = latest_url_uid
            latest["token"] = latest_url_token
            latest["log_time"] = latest_url_time
            latest["source"] = latest_url_source

    return latest


# ─── 从 shared_preferences 提取 dev_id ───────────────────────────

def extract_dev_id():
    """从 shared_preferences.json 提取 dev_id"""
    if not os.path.exists(SHARED_PREFS):
        return None
    try:
        with open(SHARED_PREFS, "r", encoding="utf-8") as f:
            prefs = json.load(f)
        return prefs.get("flutter.dev_id")
    except Exception:
        return None


# ─── 保存凭证 ─────────────────────────────────────────────────────

def save_credentials(credentials):
    """保存到本地认证文件"""
    data = {
        "uid": credentials["uid"],
        "token": credentials["token"],
        "dev_id": credentials.get("dev_id") or "",
        "nickname": credentials.get("nickname") or "",
        "auth_type": credentials.get("auth_type", 0),
        "qq_open_id": credentials.get("qq_open_id") or "",
        "qq_open_token": credentials.get("qq_open_token") or "",
        "qq_expire_time": credentials.get("qq_expire_time", 0) or 0,
        "qq_nickname": credentials.get("qq_nickname") or "",
        "qq_head_img": credentials.get("qq_head_img") or "",
        "qq_import_status": credentials.get("qq_import_status", 0) or 0,
        "qq_uid": credentials.get("qq_uid", "-1"),
        "qq_token": credentials.get("qq_token", ""),
        "qq_auth_type": credentials.get("qq_auth_type", 0),
        "qq_session_nickname": credentials.get("qq_session_nickname", ""),
        "user_is_vip": credentials.get("user_is_vip", 0),
        "vip_type": credentials.get("vip_type", 0),
        "pay_vip_type": credentials.get("pay_vip_type", 0),
        "act_vip_type": credentials.get("act_vip_type", 0),
        "pay_expire_date": credentials.get("pay_expire_date", 0),
        "act_expire_date": credentials.get("act_expire_date", 0),
        "pay_is_vip_boolean": bool(credentials.get("pay_is_vip_boolean", False)),
    }
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  已保存到: {AUTH_FILE}")


# ─── 主函数 ───────────────────────────────────────────────────────

def extract_and_show(do_save=False):
    """提取并显示凭证, 可选保存"""
    print("=" * 50)
    print("  波点音乐凭证提取工具")
    print("=" * 50)
    print()

    # 检查客户端是否安装
    if not os.path.isdir(LOCAL_DATA):
        print("  错误: 未找到波点音乐客户端数据目录")
        print(f"  期望路径: {LOCAL_DATA}")
        print("  请确认波点音乐PC版已安装并至少登录过一次")
        return None

    # 提取 dev_id
    dev_id = extract_dev_id()
    if dev_id:
        print(f"  DevID: {dev_id}")
    else:
        print("  DevID: 未找到 (不影响使用)")

    # 检查登录状态
    if os.path.exists(SHARED_PREFS):
        try:
            with open(SHARED_PREFS, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            is_login = prefs.get("flutter.isLogin", False)
            print(f"  客户端登录状态: {'已登录' if is_login else '未登录'}")
        except Exception:
            pass

    print()
    print("  从日志中提取凭证...")

    cred = extract_from_logs()

    if not cred:
        print("  未能从日志中提取到凭证")
        print("  提示: 请确保波点音乐客户端已登录, 然后重启客户端以生成新日志")
        return None

    print()
    print(f"  UID:      {cred['uid']}")
    print(f"  Token:    {cred['token']}")
    print(f"  昵称:     {cred['nickname']}")
    print(f"  会员类型: vipType={cred.get('vip_type', 0)} / payVipType={cred.get('pay_vip_type', 0)} / actVipType={cred.get('act_vip_type', 0)}")
    if cred.get("qq_open_id") and cred.get("qq_open_token"):
        print(f"  QQ音乐授权: 已提取 (openId={cred['qq_open_id']})")
    else:
        print("  QQ音乐授权: 未找到")
    print(f"  提取时间: {cred['log_time']}")
    print(f"  来源:     {cred['source']}")
    print()

    if do_save:
        save_credentials({**cred, "dev_id": dev_id})

    return {
        "uid": cred["uid"],
        "token": cred["token"],
        "dev_id": dev_id,
        "nickname": cred["nickname"],
        "auth_type": cred.get("auth_type", 0),
        "user_is_vip": cred.get("user_is_vip", 0),
        "vip_type": cred.get("vip_type", 0),
        "pay_vip_type": cred.get("pay_vip_type", 0),
        "act_vip_type": cred.get("act_vip_type", 0),
        "pay_expire_date": cred.get("pay_expire_date", 0),
        "act_expire_date": cred.get("act_expire_date", 0),
        "pay_is_vip_boolean": bool(cred.get("pay_is_vip_boolean", False)),
        "qq_open_id": cred.get("qq_open_id", ""),
        "qq_open_token": cred.get("qq_open_token", ""),
        "qq_expire_time": cred.get("qq_expire_time", 0),
        "qq_nickname": cred.get("qq_nickname", ""),
        "qq_head_img": cred.get("qq_head_img", ""),
        "qq_import_status": cred.get("qq_import_status", 0),
    }


def main():
    do_save = "--save" in sys.argv or "-s" in sys.argv

    result = extract_and_show(do_save)

    if result and not do_save:
        print("  提示: 使用 --save 参数可自动保存凭证")
        print(f"  或手动登录: python bodian_cli.py login --uid {result['uid']} --token {result['token']}")

    return result


if __name__ == "__main__":
    main()
