# Bodian

基于 Python 的第三方波点音乐客户端，提供终端界面（TUI）和命令行（CLI）两种使用方式。

## 项目特色

- 终端界面：支持搜索、推荐、收藏、歌单、艺人、专辑、播放页、歌词页和封面显示。
- 命令行操作：支持登录、搜索、元数据查询、歌词保存、单曲下载、艺人专辑批量下载和 JSON 输出。
- 登录方式：支持二维码登录、从已安装的波点 PC 客户端提取登录信息，也支持手动填写 UID 与 Token。
- 播放控制：使用本机 `ffplay` 播放，支持暂停、继续、上一首、下一首、停止、重播和进度定位。
- 歌词与封面：可查看歌词，保存 `.lrc` 文件，并在支持的终端中显示封面。
- 下载整理：下载音频时可写入封面，按艺人、专辑整理批量下载结果，并保存元数据。
- 音质选择：支持 MP3、OGG、FLAC、AAC 以及部分私有格式的音质编号选择。

## 运行环境

- Python 3.10 或更高版本。
- FFmpeg：播放需要 `ffplay`，写入封面需要 `ffmpeg`。
- Python 依赖：`urwid`、`Pillow`、`textual-image`；二维码终端显示可选安装 `qrcode`。

安装依赖：

```bash
pip install urwid pillow textual-image qrcode
```

请确保 `ffmpeg` 和 `ffplay` 已加入系统 `PATH`。

## 快速用法

### 1. 登录

二维码登录：

```bash
python bodian_cli.py login
```

从已安装并已登录的波点 PC 客户端提取登录信息：

```bash
python bodian_cli.py login --extract
```

手动填写登录信息：

```bash
python bodian_cli.py login --uid UID --token TOKEN
```

查看当前登录状态：

```bash
python bodian_cli.py auth
```

### 2. 启动终端界面

```bash
python bodian_ui.py
```

常用快捷键：

| 快捷键 | 用途 |
| --- | --- |
| `/` | 搜索 |
| `Enter` | 执行当前操作或打开当前项目 |
| `Space` | 播放或暂停 |
| `n` / `p` | 下一首 / 上一首 |
| `s` / `r` | 停止 / 重播 |
| `[` / `]` | 后退 10 秒 / 前进 10 秒 |
| `d` | 下载当前歌曲 |
| `l` | 保存当前歌词 |
| `v` | 进入播放页 |
| `Esc` | 返回 |
| `q` | 退出 |

### 3. 使用命令行

```bash
python bodian_cli.py search 周杰伦 -l 10
python bodian_cli.py download 周杰伦 -i 1 -q 6 -o downloads
python bodian_cli.py lyric 周杰伦 -i 1 --save -o downloads
python bodian_cli.py download-artist ARTIST_ID -q 6 -o downloads
```

不带子命令执行时会进入交互式命令行：

```bash
python bodian_cli.py
```

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `python bodian_cli.py auth` | 查看登录状态 |
| `python bodian_cli.py logout` | 删除本地登录信息 |
| `python bodian_cli.py search 关键词` | 搜索歌曲 |
| `python bodian_cli.py meta artist ARTIST_ID` | 查询艺人信息 |
| `python bodian_cli.py meta artist-music ARTIST_ID` | 查询艺人歌曲 |
| `python bodian_cli.py meta artist-albums ARTIST_ID` | 查询艺人专辑 |
| `python bodian_cli.py meta album ALBUM_ID` | 查询专辑信息 |
| `python bodian_cli.py meta album-music ALBUM_ID` | 查询专辑歌曲 |
| `python bodian_cli.py meta playlist PLAYLIST_ID` | 查询歌单信息 |
| `python bodian_cli.py meta playlist-music PLAYLIST_ID` | 查询歌单歌曲 |
| `python bodian_cli.py my fond` | 查询喜欢的音乐 |
| `python bodian_cli.py my fond-playlist` | 查询收藏歌单 |
| `python bodian_cli.py my created` | 查询自己创建的歌单 |
| `python bodian_cli.py my collected` | 查询已收藏歌单 |
| `python bodian_cli.py my artists` | 查询关注艺人 |
| `python bodian_cli.py my history-db` | 查询本地播放历史 |
| `python bodian_cli.py my favorites-db` | 查询本地收藏缓存 |
| `python bodian_cli.py write collect --id MUSIC_ID` | 收藏歌曲 |
| `python bodian_cli.py write uncollect --id MUSIC_ID` | 取消收藏歌曲 |

## 高级参数

### 登录与状态

| 命令 | 参数 | 说明 |
| --- | --- | --- |
| `auth` | `--json` | 以 JSON 输出登录状态 |
| `login` | `--wait 秒数` | 设置二维码登录等待时间，默认 120 秒 |
| `login` | `--extract` | 从已安装的波点 PC 客户端提取登录信息 |
| `login` | `--uid UID --token TOKEN` | 手动设置登录信息 |

### 查询

| 命令 | 参数 | 说明 |
| --- | --- | --- |
| `search 关键词` | `-l, --limit 数量` | 设置搜索结果数量，默认 20 |
| `search 关键词` | `--json` | 同时输出 JSON 结果 |
| `meta 类型 ID` | `-p, --page 页码` | 设置页码，默认 1 |
| `meta 类型 ID` | `-l, --limit 数量` | 设置每页数量，默认 50 |
| `meta playlist-music ID` | `--source 来源编号` | 设置歌单来源编号，默认 5 |
| `my 类型` | `-p, --page 页码` | 设置页码，默认 1 |
| `my 类型` | `-l, --limit 数量` | 设置数量，默认 20 |

`meta` 支持的类型：`artist`、`artist-music`、`artist-albums`、`album`、`album-music`、`playlist`、`playlist-music`。

`my` 支持的类型：`fond`、`fond-playlist`、`created`、`collected`、`artists`、`history-db`、`favorites-db`。

### 下载与歌词

| 命令 | 参数 | 说明 |
| --- | --- | --- |
| `download 关键词` / `dl 关键词` | `-i, --index 序号` | 选择第几个搜索结果，默认 1 |
| `download 关键词` / `dl 关键词` | `-q, --quality 编号` | 指定下载音质；不指定时使用本地偏好 |
| `download 关键词` / `dl 关键词` | `-o, --output 目录` | 指定输出目录 |
| `download 关键词` / `dl 关键词` | `--free-sign 值` | 手动提供 `freeSign` |
| `download-artist ARTIST_ID` | `-q, --quality 编号` | 指定目标音质，默认 6 |
| `download-artist ARTIST_ID` | `-o, --output 目录` | 指定输出根目录 |
| `lyric 关键词` | `-i, --index 序号` | 选择第几个搜索结果，默认 1 |
| `lyric 关键词` | `-o, --output 目录` | 指定歌词保存目录 |
| `lyric 关键词` | `--save` | 保存为 `.lrc` 文件 |
| `lyric 关键词` | `--json` | 以 JSON 输出歌词解析结果 |

### 接口检查

`probe` 用于查看推荐、首页、榜单、权限、音频地址等返回内容，适合需要 JSON 结果时使用。

```bash
python bodian_cli.py probe recommend -p 1 -l 20
python bodian_cli.py probe audio-url --id MUSIC_ID -q 6
python bodian_cli.py probe check-right --id MUSIC_ID --free-sign FREE_SIGN
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--id ID` | 指定歌曲、艺人、歌单或模块 ID |
| `--free-sign 值` | 指定 `freeSign` |
| `-q, --quality 编号` | 指定音质编号 |
| `-p, --page 页码` | 指定页码或滚动编号 |
| `-l, --limit 数量` | 指定数量 |

## 音质编号

| 编号 | 格式 | 说明 |
| --- | --- | --- |
| 1 | MP3 | 128kbps |
| 2 | MP3 | 320kbps |
| 3 | OGG | 100kbps |
| 4 | OGG | 192kbps |
| 5 | OGG | 300kbps |
| 6 | FLAC | 无损 |
| 7 | MFLAC | 20201kbps，ZPGA201 |
| 8 | AAC | 48kbps |
| 9 | MFLAC | 20501kbps，ZPGA501 |
| 10 | MFLAC | 20900kbps，ZPLY |
| 11 | MGG | 22000kbps，BCMS |
| 12 | ZP | 20000kbps |

播放依赖 `ffplay`，本机无法解码的格式不能直接播放。单曲下载指定不可用音质时会显示当前歌曲支持的音质；批量下载会在终端显示每首歌曲的实际使用音质。

## 本地数据

默认会在当前目录写入以下本地数据：

| 路径 | 用途 |
| --- | --- |
| `.bodian/auth.json` | 登录信息 |
| `.bodian/config.json` | 下载目录、播放音质和下载音质偏好 |
| `downloads/` | 下载的音频、封面、歌词和元数据 |

请不要公开分享 `.bodian/` 目录内容。该目录已在 `.gitignore` 中排除。

## 许可

本项目使用 MIT License。
