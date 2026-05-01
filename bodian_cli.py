#!/usr/bin/env python3
"""
波点音乐命令行工具
CTF Competition - Authorized Security Research
"""

import argparse
import json
import os
import sys
import time

from bodian_toolkit import (
    AUTH_FILE,
    DEFAULT_DOWNLOAD_DIR,
    QUALITY_OPTIONS,
    BoDianClient,
    _detect_ext,
    _fmt_dur,
    _print_qr_terminal,
    _sanitize,
)


def _print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _print_auth_summary(client):
    auth = client.get_auth_state()
    main = auth["main"]
    audio = auth["audio_session"]
    qq_music_auth = auth["qq_music_auth"]
    qq_session = auth["qq_session"]
    print(f"  主账号: {main['nickname'] or main['uid']} (UID={main['uid']}, authType={main['auth_type']})")
    print(f"  DevID: {main['dev_id']}")
    if qq_music_auth["open_id"]:
        print(f"  QQ音乐授权: 已提取 (openId={qq_music_auth['open_id']})")
    else:
        print("  QQ音乐授权: 未提取")
    print(f"  播放会话: {audio['nickname'] or audio['uid']} (UID={audio['uid']}, authType={audio['auth_type']})")
    if qq_session["ready"]:
        print(f"  已缓存QQ会话: {qq_session['nickname'] or qq_session['uid']} (UID={qq_session['uid']}, authType={qq_session['auth_type']})")


def _pick_song_from_search(client, keyword, index):
    results = client.search(keyword, page_size=max(index, 20))
    if not results:
        return None
    return results[min(index - 1, len(results) - 1)]


def _compact_date(value):
    digits = "".join(ch for ch in str(value or "").strip() if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _build_album_dir_name(album_info, album_id):
    album_name = str(album_info.get("name") or album_id).strip()
    compact_date = _compact_date(album_info.get("showtime") or album_info.get("releaseDate"))
    return _sanitize(f"[{compact_date}]{album_name}" if compact_date else album_name)


def cmd_meta(args, client):
    if args.kind == "artist":
        data = client.get_artist(args.id)
    elif args.kind == "artist-music":
        data = client.get_artist_music(args.id, page=args.page, page_size=args.limit)
    elif args.kind == "artist-albums":
        data = client.get_artist_albums(args.id, page=args.page, page_size=args.limit)
    elif args.kind == "album":
        data = client.get_album(args.id)
    elif args.kind == "album-music":
        data = client.get_album_music(args.id, page=args.page, page_size=args.limit)
    elif args.kind == "playlist":
        data = client.get_playlist_info(args.id, source=args.source)
    elif args.kind == "playlist-music":
        data = client.get_playlist_music(args.id, page=args.page, page_size=args.limit, source=args.source)
    else:
        data = {"error": f"unknown kind: {args.kind}"}
    _print_json(data)


def cmd_my(args, client):
    if args.kind == "fond":
        data = client.get_my_fond_songs(page=args.page, page_size=args.limit)
    elif args.kind == "fond-playlist":
        data = client.get_my_fond_playlist()
    elif args.kind == "created":
        data = client.get_my_created_playlists()
    elif args.kind == "collected":
        data = client.get_my_collected_playlists(page=args.page, page_size=args.limit)
    elif args.kind == "artists":
        data = client.get_followed_artists(page=args.page, page_size=args.limit)
    elif args.kind == "history-db":
        data = client.get_history_db_snapshot(limit=args.limit)
    elif args.kind == "favorites-db":
        data = client.get_favorites_db_snapshot(limit=args.limit)
    else:
        data = {"error": f"unknown kind: {args.kind}"}
    _print_json(data)


def cmd_probe(args, client):
    if args.kind in ("home-module", "bang", "check-right", "audio-url") and not args.id:
        _print_json({"error": f"{args.kind} 需要 --id"})
        return
    if args.kind == "user":
        _print_json(client.get_user_profile(args.id or client.uid))
        return
    if args.kind == "recommend":
        data, resp = client.get_recommendations(scroll_num=args.page, total_num=args.limit)
        _print_json({"data": data, "resp": resp})
        return
    if args.kind == "home-index":
        _print_json(client.get_home_index())
        return
    if args.kind == "home-module":
        _print_json(client.get_home_module(args.id))
        return
    if args.kind == "bang":
        _print_json(client.get_bang_music(args.id, page=args.page, page_size=args.limit))
        return
    if args.kind == "advert":
        _print_json(client.get_advert_config())
        return
    if args.kind == "paid-card":
        _print_json(client.get_paid_card_info())
        return
    if args.kind == "pay-products":
        _print_json(client.get_pay_products())
        return
    if args.kind == "purchased-albums":
        _print_json(client.get_purchased_albums(page=args.page, page_size=args.limit))
        return
    if args.kind == "pc-conf":
        _print_json(client.get_pc_config())
        return
    if args.kind == "version-popup":
        _print_json(client.get_version_popup())
        return
    if args.kind == "version-check":
        _print_json(client.get_version_check())
        return
    if args.kind == "tips":
        _print_json(client.search_tips(args.keyword or ""))
        return
    if args.kind == "hot-topics":
        _print_json(client.hot_topics())
        return
    if args.kind == "artists":
        _print_json(client.search_artists(args.keyword or "", page=args.page, page_size=args.limit))
        return
    if args.kind == "playlists":
        _print_json(client.search_playlists(args.keyword or "", page=args.page, page_size=args.limit))
        return
    if args.kind == "check-right":
        data, resp = client.check_right_with_freesign(args.id, args.free_sign or "")
        _print_json({"data": data, "resp": resp})
        return
    if args.kind == "audio-url":
        fmt, br, desc = QUALITY_OPTIONS.get(args.quality, QUALITY_OPTIONS["4"])
        url, err = client.get_audio_url(args.id, fmt, br, free_sign=args.free_sign or "")
        _print_json({"quality": desc, "url": url, "error": err})
        return
    _print_json({"error": f"unknown probe kind: {args.kind}"})


def cmd_write(args, client):
    if args.kind == "collect":
        data, resp = client.collect_song(args.id)
        _print_json({"data": data, "resp": resp})
        return
    if args.kind == "uncollect":
        data, resp = client.uncollect_song(args.id)
        _print_json({"data": data, "resp": resp})
        return
    _print_json({"error": f"unknown write kind: {args.kind}"})


def cmd_auth(args, client):
    if args.json:
        _print_json(client.get_auth_state())
        return
    _print_auth_summary(client)


def cmd_lyric(args, client):
    song = _pick_song_from_search(client, args.keyword, args.index)
    if not song:
        print("  无搜索结果")
        sys.exit(1)

    lyric = client.get_lyric(song["id"], song["name"], song["artist"])
    print(f"  歌曲: {song['name']} - {song['artist']}")
    if args.save or args.output:
        output_dir = args.output or client.get_local_config("download_dir", DEFAULT_DOWNLOAD_DIR)
        os.makedirs(output_dir, exist_ok=True)
        filepath = os.path.join(output_dir, _sanitize(f"{song['artist']} - {song['name']}.lrc"))
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(lyric["raw"])
        print(f"  歌词已保存: {filepath}")
    if args.json:
        _print_json(lyric)
    else:
        print()
        print(lyric["raw"] or "  暂无歌词")


def cmd_extract(args, client):
    if client.extract_from_client():
        print(f"\n  登录成功: {client.nickname} (UID={client.uid})")
        _print_auth_summary(client)
        print(f"  认证信息已保存到: {AUTH_FILE}")
    else:
        print("  提取失败, 请确保波点客户端已登录")
        print("  或手动指定: python bodian_cli.py login --uid UID --token TOKEN")


def cmd_login(args, client):
    if args.uid and args.token:
        client.set_credentials(args.uid, args.token)
        print(f"  已设置凭证: UID={client.uid}")
        _print_auth_summary(client)
        print(f"  已保存到: {AUTH_FILE}")
        return

    if args.extract:
        print("  尝试从波点客户端自动提取凭证...")
        if client.extract_from_client():
            print(f"\n  登录成功: {client.nickname} (UID={client.uid})")
            _print_auth_summary(client)
            print(f"  认证信息已保存到: {AUTH_FILE}")
        else:
            print("  自动提取失败")
            print("  请手动指定: python bodian_cli.py login --uid UID --token TOKEN")
        return

    print("  正在请求二维码登录...")
    old_uid = client.uid
    old_token = client.token
    old_nickname = client.nickname
    old_logged_in = client.logged_in
    client.uid = "-1"
    client.token = ""
    client.nickname = ""
    client.logged_in = False

    qr_data, qr_resp = client.request_login_qr()
    qr_code = qr_data.get("qrCode") if qr_data else ""
    if not qr_code:
        client.uid = old_uid
        client.token = old_token
        client.nickname = old_nickname
        client.logged_in = old_logged_in
        print(f"  获取二维码失败: {qr_resp}")
        return

    print("  请使用波点移动端扫描下方二维码完成登录：")
    print(f"  二维码载荷: {qr_code}")
    _print_qr_terminal(qr_code)

    deadline = time.time() + max(args.wait, 1)
    last_status = None
    while time.time() < deadline:
        status_data, status_resp = client.check_login_qr(qr_code)
        status = status_data.get("status") if status_data else None
        if status != last_status:
            if status == 1:
                print("  当前状态: 等待移动端确认")
            elif status == 3:
                print("  当前状态: 已确认，正在换取凭证")
            else:
                print(f"  当前状态: {status}")
            last_status = status
        if status == 3:
            login_data, login_resp = client.users_login(uid="-1", token="")
            if login_data and client.logged_in:
                print(f"  登录成功: {client.nickname or client.uid} (UID={client.uid})")
                _print_auth_summary(client)
                print(f"  认证信息已保存到: {AUTH_FILE}")
            else:
                client.uid = old_uid
                client.token = old_token
                client.nickname = old_nickname
                client.logged_in = old_logged_in
                print(f"  换取凭证失败: {login_resp}")
            return
        if status not in (None, 1):
            client.uid = old_uid
            client.token = old_token
            client.nickname = old_nickname
            client.logged_in = old_logged_in
            print(f"  登录未完成，状态码: {status}")
            print(f"  服务端响应: {status_resp}")
            return
        time.sleep(2)

    client.uid = old_uid
    client.token = old_token
    client.nickname = old_nickname
    client.logged_in = old_logged_in
    print("  等待移动端确认超时，请重新执行 login")


def cmd_search(args, client):
    results = client.search(args.keyword, page_size=args.limit)
    if not results:
        print("  无搜索结果")
        return

    print(f"\n  {'#':<4} {'歌曲名':<30} {'歌手':<20} {'时长':<8} {'专辑'}")
    print("  " + "-" * 85)
    for i, song in enumerate(results, 1):
        print(f"  {i:<4} {song['name'][:28]:<30} {song['artist'][:18]:<20} {_fmt_dur(song['duration']):<8} {song['album'][:25]}")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))


def cmd_download(args, client):
    song = _pick_song_from_search(client, args.keyword, args.index)
    if not song:
        print("  无搜索结果")
        sys.exit(1)
    choices = song.get("quality_choices") or client.get_song_quality_choices(song)
    if not choices:
        print("  当前歌曲没有可用音质")
        sys.exit(1)
    available_keys = {choice["key"] for choice in choices}
    quality_key = args.quality or client.resolve_song_quality(song, client.get_local_config("quality", "6"))
    if args.quality and args.quality not in available_keys:
        print(f"  当前歌曲不支持音质 {args.quality}")
        print("  可用音质:")
        for choice in choices:
            print(f"    {choice['key']}. {choice['label']}")
        sys.exit(1)
    if not quality_key:
        print("  当前歌曲没有可用音质")
        sys.exit(1)
    fmt, br, desc = QUALITY_OPTIONS[quality_key]
    output_dir = args.output or client.get_local_config("download_dir", DEFAULT_DOWNLOAD_DIR)

    print(f"  歌曲: {song['name']} - {song['artist']}")
    print(f"  音质: {desc}")

    audio_url, actual_fmt, audio_error = client.get_audio_url_with_fallback(
        song["id"], fmt, br, free_sign=args.free_sign or song.get("freeSign") or ""
    )
    if audio_error:
        print(f"  {audio_error}")
        sys.exit(1)
    if not audio_url:
        print("  无法获取下载链接")
        sys.exit(1)
    if actual_fmt != fmt:
        print(f"  服务端实际返回: {actual_fmt.upper()}")
    if "/pay3_v2/" in audio_url:
        print("  当前歌曲仅提供试听片段，将保存试听版本")

    os.makedirs(output_dir, exist_ok=True)
    ext = _detect_ext(actual_fmt, audio_url)
    filename = _sanitize(f"{song['artist']} - {song['name']}.{ext}")
    filepath = os.path.join(output_dir, filename)

    print(f"  URL: {audio_url[:80]}...")
    if client.download(audio_url, filepath, song=song):
        client.set_local_config(download_dir=output_dir, quality=quality_key)


def cmd_download_artist(args, client):
    artist_data = client.get_artist(args.artist_id) or {}
    artist_info = artist_data.get("artistInfo", {})
    if not artist_info:
        print("  无法获取歌手信息")
        sys.exit(1)

    albums = client.get_all_artist_album_items(args.artist_id)
    if not albums:
        print("  当前歌手没有可下载专辑")
        sys.exit(1)

    artist_name = artist_info.get("name") or str(args.artist_id)
    base_output_dir = args.output or client.get_local_config("download_dir", DEFAULT_DOWNLOAD_DIR)
    output_root = os.path.join(base_output_dir, _sanitize(artist_name))
    os.makedirs(output_root, exist_ok=True)

    with open(os.path.join(output_root, "artist.json"), "w", encoding="utf-8") as f:
        json.dump(artist_info, f, ensure_ascii=False, indent=2)

    print(f"  歌手: {artist_name}")
    print(f"  专辑数: {len(albums)}")
    print(f"  输出目录: {output_root}")

    seen_album_dirs = set()
    total_tracks = 0
    ok_tracks = 0
    failed_tracks = []

    for album in albums:
        album_id = album.get("albumId") or album.get("id")
        if not album_id:
            continue
        album_data = client.get_album(album_id) or {}
        album_info = album_data.get("albumInfo") or album
        songs = client.get_all_album_song_list(album_id)
        if not songs:
            print(f"\n  跳过专辑: {album_info.get('name') or album_id} (无曲目)")
            continue

        album_name = album_info.get("name") or str(album_id)
        album_date = album_info.get("showtime") or album_info.get("releaseDate") or ""
        album_dir_name = _build_album_dir_name(album_info, album_id)
        if album_dir_name in seen_album_dirs:
            album_dir_name = _sanitize(f"{album_dir_name} [{album_id}]")
        seen_album_dirs.add(album_dir_name)
        album_dir = os.path.join(output_root, album_dir_name)
        os.makedirs(album_dir, exist_ok=True)
        compact_album_date = _compact_date(album_date)
        cover_path = ""
        cover_url = album_info.get("pic") or album_info.get("albumPic") or album_info.get("albumPic120") or ""
        if cover_url:
            cover_path = os.path.join(album_dir, f"cover{client._guess_cover_suffix(cover_url)}")
            try:
                with open(cover_path, "wb") as f:
                    f.write(client._request_binary(cover_url))
            except Exception as e:
                failed_tracks.append(f"{album_name} / cover ({e})")
                print(f"    封面保存失败: {e}")
                cover_path = ""

        album_manifest = dict(album_info)
        album_manifest["directoryName"] = album_dir_name
        album_manifest["directoryPath"] = album_dir
        if compact_album_date:
            album_manifest["releaseDateCompact"] = compact_album_date
        if cover_path:
            album_manifest["coverPath"] = cover_path
        with open(os.path.join(album_dir, "album.json"), "w", encoding="utf-8") as f:
            json.dump(album_manifest, f, ensure_ascii=False, indent=2)

        track_total = len(songs)
        track_width = max(2, len(str(track_total)))
        tracks_manifest = []

        print(f"\n  专辑: {album_name} ({track_total} 首)")

        for index, raw_song in enumerate(songs, 1):
            total_tracks += 1
            song = dict(raw_song)
            song["album"] = album_name
            song["albumId"] = album_id
            song["albumArtist"] = album_info.get("artist") or artist_name
            song["albumPic"] = song.get("albumPic") or album_info.get("pic", "")
            song["albumPic120"] = song.get("albumPic120") or album_info.get("pic", "")
            song["releaseDate"] = song.get("releaseDate") or album_date
            song["trackNumber"] = index
            song["trackTotal"] = track_total
            song["discNumber"] = 1
            song["discTotal"] = 1
            if not song.get("artists"):
                song["artists"] = album_info.get("artists", []) or [{
                    "id": song.get("artistId") or artist_info.get("id"),
                    "name": song.get("artist") or artist_name,
                }]

            choices = song.get("quality_choices") or client.get_song_quality_choices(song)
            if not choices:
                failed_tracks.append(f"{album_name} / {song['name']} (无可用音质)")
                print(f"    {index:0{track_width}d}. {song['name']} -> 无可用音质")
                continue

            available_keys = {choice["key"] for choice in choices}
            quality_key = args.quality or client.resolve_song_quality(song, client.get_local_config("quality", "6"))
            if args.quality and args.quality not in available_keys:
                quality_key = client.resolve_song_quality(song, args.quality)
                print(
                    f"    {index:0{track_width}d}. {song['name']} -> 请求 {QUALITY_OPTIONS[args.quality][2]}，"
                    f"当前歌曲改用 {QUALITY_OPTIONS[quality_key][2]}"
                )
            if not quality_key:
                failed_tracks.append(f"{album_name} / {song['name']} (无法决定音质)")
                print(f"    {index:0{track_width}d}. {song['name']} -> 无法决定音质")
                continue

            fmt, br, desc = QUALITY_OPTIONS[quality_key]
            audio_url, actual_fmt, audio_error = client.get_audio_url_with_fallback(
                song["id"], fmt, br, free_sign=song.get("freeSign") or ""
            )
            if audio_error:
                failed_tracks.append(f"{album_name} / {song['name']} ({audio_error})")
                print(f"    {index:0{track_width}d}. {song['name']} -> {audio_error}")
                continue
            if not audio_url:
                failed_tracks.append(f"{album_name} / {song['name']} (无下载链接)")
                print(f"    {index:0{track_width}d}. {song['name']} -> 无下载链接")
                continue

            ext = _detect_ext(actual_fmt, audio_url)
            file_stem = _sanitize(f"{index:0{track_width}d}. {song['name']}")
            filepath = os.path.join(album_dir, f"{file_stem}.{ext}")
            lyric_path = os.path.join(album_dir, f"{file_stem}.lrc")
            print(f"    {index:0{track_width}d}. {song['name']} -> {desc}", end="")
            if actual_fmt != fmt:
                print(f" / 服务端实际返回 {actual_fmt.upper()}", end="")
            print()

            if client.download(audio_url, filepath, song=song):
                lyric_saved = False
                try:
                    lyric = client.save_lyric(song, lyric_path)
                    lyric_saved = bool(lyric.get("raw"))
                    if not lyric_saved:
                        if os.path.exists(lyric_path):
                            os.remove(lyric_path)
                        print(f"    {index:0{track_width}d}. {song['name']} -> 歌词为空")
                except Exception as e:
                    if os.path.exists(lyric_path):
                        os.remove(lyric_path)
                    failed_tracks.append(f"{album_name} / {song['name']} (歌词保存失败: {e})")
                    print(f"    {index:0{track_width}d}. {song['name']} -> 歌词保存失败: {e}")
                ok_tracks += 1
                tracks_manifest.append({
                    "trackNumber": index,
                    "trackTotal": track_total,
                    "id": song["id"],
                    "name": song["name"],
                    "artist": song["artist"],
                    "album": song["album"],
                    "releaseDate": song.get("releaseDate", ""),
                    "musicRid": song.get("musicRid", ""),
                    "qualityRequested": desc,
                    "formatSaved": actual_fmt,
                    "path": filepath,
                    "lyricPath": lyric_path if lyric_saved else "",
                    "coverPath": cover_path,
                })
            else:
                failed_tracks.append(f"{album_name} / {song['name']} (下载失败)")

        with open(os.path.join(album_dir, "tracks.json"), "w", encoding="utf-8") as f:
            json.dump(tracks_manifest, f, ensure_ascii=False, indent=2)

    client.set_local_config(download_dir=base_output_dir, quality=args.quality or client.get_local_config("quality", "6"))
    print(f"\n  完成: 成功 {ok_tracks}/{total_tracks}")
    if failed_tracks:
        print("  失败列表:")
        for item in failed_tracks:
            print(f"    {item}")


def cmd_interactive(args, client):
    print("=" * 50)
    print("  波点音乐工具")
    print("  CTF Competition - Security Research")
    print("=" * 50)

    if client.logged_in:
        nick = client.nickname or client.uid
        print(f"  已登录: {nick} (UID={client.uid})")
    else:
        print("  未登录 | 输入 'login' 发起二维码登录")

    while True:
        print()
        command = input("  > 搜索关键词 (q=退出, login=登录, logout=登出): ").strip()
        if not command:
            continue
        if command.lower() == "q":
            break
        if command.lower() == "login":
            cmd_login(argparse.Namespace(uid=None, token=None, extract=False, wait=120), client)
            continue
        if command.lower() == "logout":
            client.logout()
            continue

        results = client.search(command)
        if not results:
            print("  无结果")
            continue

        print(f"\n  {'#':<4} {'歌曲':<30} {'歌手':<20} {'时长':<8} {'格式'}")
        print("  " + "-" * 85)
        for i, song in enumerate(results, 1):
            print(f"  {i:<4} {song['name'][:28]:<30} {song['artist'][:18]:<20} {_fmt_dur(song['duration']):<8} {song['formats_str'][:25]}")

        while True:
            selected = input("\n  选择编号 (b=返回): ").strip()
            if selected.lower() == "b":
                break
            try:
                idx = int(selected) - 1
                if not (0 <= idx < len(results)):
                    print("  无效")
                    continue
            except ValueError:
                continue

            song = results[idx]
            print(f"\n  {song['name']} - {song['artist']}")
            print("  音质选项:")
            choices = client.get_song_quality_choices(song)
            if not choices:
                print("    当前歌曲没有可用音质")
                continue
            for choice in choices:
                print(f"    {choice['key']}. {choice['label']}")
            default_quality = client.resolve_song_quality(song, client.get_local_config("quality", "6")) or choices[0]["key"]
            quality = input(f"  选择音质 [{'/'.join(choice['key'] for choice in choices)}] (默认{default_quality}): ").strip() or default_quality
            if quality not in {choice['key'] for choice in choices}:
                print("  所选音质不在当前歌曲可用范围内")
                continue
            fmt, br, _ = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS[default_quality])
            output_dir = client.get_local_config("download_dir", DEFAULT_DOWNLOAD_DIR)
            audio_url, actual_fmt, audio_error = client.get_audio_url_with_fallback(
                song["id"], fmt, br, free_sign=song.get("freeSign") or ""
            )

            if audio_error:
                print(f"  {audio_error}")
            elif audio_url:
                if actual_fmt != fmt:
                    print(f"  服务端实际返回: {actual_fmt.upper()}")
                if "/pay3_v2/" in audio_url:
                    print("  当前歌曲仅提供试听片段，将保存试听版本")
                os.makedirs(output_dir, exist_ok=True)
                ext = _detect_ext(actual_fmt, audio_url)
                filename = _sanitize(f"{song['artist']} - {song['name']}.{ext}")
                filepath = os.path.join(output_dir, filename)
                if client.download(audio_url, filepath, song=song):
                    client.set_local_config(download_dir=output_dir, quality=quality)
            else:
                print("  无法获取链接")

    print("  再见!")


def main():
    parser = argparse.ArgumentParser(
        prog="bodian",
        description="波点音乐命令行工具",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("extract", help="从波点 PC 客户端自动提取凭证")
    p_auth = sub.add_parser("auth", help="查看当前本地认证状态")
    p_auth.add_argument("--json", action="store_true", help="输出 JSON")

    p_login = sub.add_parser("login", help="二维码登录、手动登录或从客户端提取")
    p_login.add_argument("--uid", default=None, help="手动指定 UID")
    p_login.add_argument("--token", default=None, help="手动指定 Token")
    p_login.add_argument("--extract", action="store_true", help="改为从已安装客户端提取凭证")
    p_login.add_argument("--wait", type=int, default=120, help="二维码登录等待秒数")

    sub.add_parser("logout", help="登出并删除本地认证信息")

    p_search = sub.add_parser("search", help="搜索歌曲")
    p_search.add_argument("keyword", help="搜索关键词")
    p_search.add_argument("-l", "--limit", type=int, default=20, help="结果数量")
    p_search.add_argument("--json", action="store_true", help="输出 JSON")

    p_meta = sub.add_parser("meta", help="查询艺人、专辑、歌单等元数据")
    p_meta.add_argument("kind", choices=["artist", "artist-music", "artist-albums", "album", "album-music", "playlist", "playlist-music"])
    p_meta.add_argument("id", help="对应的 artist、album、playlist ID")
    p_meta.add_argument("-p", "--page", type=int, default=1, help="页码")
    p_meta.add_argument("-l", "--limit", type=int, default=50, help="每页数量")
    p_meta.add_argument("--source", type=int, default=5, help="playlist source")

    p_my = sub.add_parser("my", help="查询我的收藏、歌单、本地缓存")
    p_my.add_argument("kind", choices=["fond", "fond-playlist", "created", "collected", "artists", "history-db", "favorites-db"])
    p_my.add_argument("-p", "--page", type=int, default=1, help="页码")
    p_my.add_argument("-l", "--limit", type=int, default=20, help="数量")

    p_probe = sub.add_parser("probe", help="探测配置、推荐、首页、榜单、权限等接口")
    p_probe.add_argument("kind", choices=["user", "recommend", "home-index", "home-module", "bang", "advert", "paid-card", "pay-products", "purchased-albums", "pc-conf", "version-popup", "version-check", "tips", "hot-topics", "artists", "playlists", "check-right", "audio-url"])
    p_probe.add_argument("keyword", nargs="?", default=None, help="关键词")
    p_probe.add_argument("--id", default=None, help="music、artist、playlist 等 ID")
    p_probe.add_argument("--free-sign", default=None, help="日志里抓到的 freeSign")
    p_probe.add_argument("-q", "--quality", default="4", choices=list(QUALITY_OPTIONS.keys()), help="audio-url 使用的音质编号")
    p_probe.add_argument("-p", "--page", type=int, default=1, help="页码或 scrollNum")
    p_probe.add_argument("-l", "--limit", type=int, default=20, help="数量")

    p_download = sub.add_parser("download", aliases=["dl"], help="搜索并下载歌曲")
    p_download.add_argument("keyword", help="搜索关键词")
    p_download.add_argument("-q", "--quality", default=None, choices=list(QUALITY_OPTIONS.keys()), help="音质编号，缺省时按本地偏好匹配当前歌曲可用音质")
    p_download.add_argument("-i", "--index", type=int, default=1, help="第几个搜索结果 (默认1)")
    p_download.add_argument("-o", "--output", default=None, help="输出目录，缺省时使用本地配置")
    p_download.add_argument("--free-sign", default=None, help="手动提供 freeSign")

    p_download_artist = sub.add_parser("download-artist", help="按歌手批量下载全部专辑")
    p_download_artist.add_argument("artist_id", help="歌手 ID")
    p_download_artist.add_argument("-q", "--quality", default="6", choices=list(QUALITY_OPTIONS.keys()), help="目标音质，当前歌曲不支持时会显式改用其最佳可用音质")
    p_download_artist.add_argument("-o", "--output", default=None, help="输出根目录，缺省时使用本地配置")

    p_lyric = sub.add_parser("lyric", help="搜索并查看或保存歌词")
    p_lyric.add_argument("keyword", help="搜索关键词")
    p_lyric.add_argument("-i", "--index", type=int, default=1, help="第几个搜索结果 (默认1)")
    p_lyric.add_argument("-o", "--output", default=None, help="保存目录，缺省时使用本地配置")
    p_lyric.add_argument("--save", action="store_true", help="同时保存为 .lrc 文件")
    p_lyric.add_argument("--json", action="store_true", help="输出 JSON")

    p_write = sub.add_parser("write", help="执行收藏、取消收藏等写操作")
    p_write.add_argument("kind", choices=["collect", "uncollect"])
    p_write.add_argument("--id", required=True, help="歌曲 ID")

    args = parser.parse_args()
    client = BoDianClient()

    if args.command == "extract":
        cmd_extract(args, client)
    elif args.command == "auth":
        cmd_auth(args, client)
    elif args.command == "login":
        cmd_login(args, client)
    elif args.command == "logout":
        client.logout()
    elif args.command == "search":
        cmd_search(args, client)
    elif args.command == "meta":
        cmd_meta(args, client)
    elif args.command == "my":
        cmd_my(args, client)
    elif args.command == "probe":
        cmd_probe(args, client)
    elif args.command == "write":
        cmd_write(args, client)
    elif args.command in ("download", "dl"):
        cmd_download(args, client)
    elif args.command == "download-artist":
        cmd_download_artist(args, client)
    elif args.command == "lyric":
        cmd_lyric(args, client)
    elif args.command is None:
        cmd_interactive(args, client)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
