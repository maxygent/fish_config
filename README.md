# fishkit

从一台只有 bash 的机器开始，把 **fish、提示符皮肤、两个包管理工具** 一次装好。
缺 `fish` / `python3` / `fzf` / `curl` / `starship` 都会自己补，登录 shell 也一并改成 fish。

```bash
bash install.sh          # 系统安装（会要 sudo），装完 exec fish 就完事
```

装的是**实体副本**，不是指回仓库的软链：之后 `rm -rf ~/fishkit` 也不影响
`pkg` / `pkgview` / 提示符。想改成只装当前用户，加 `--user`。

- **外观** 提示符取自 [NyxNiri_fork](https://gitee.com/lingzore/NyxNiri_fork) 的 `starship.toml`
  （紫调、`╭╴ … / ╰─❯` 两行），没装 starship 时自动退回自带 montys 主题
- **`pkg`** 一个命令管三件事：看已装了什么、fzf 搜包、装/卸
- **两个 TUI** `pkgview`（已安装程序浏览）和 `aptui`（apt 包搜索安装），纯标准库、零依赖

## 目录

```
fishkit/
├── install.sh                     # 一键安装 / 卸载 / 自测
├── bin/
│   ├── pkgview.py                 # 已安装程序 TUI（apt/snap/flatpak/pip/PATH）
│   └── aptui.py                   # apt 包搜索安装 TUI（+ fzf 用的 --plain/--show）
├── fish/
│   ├── config.fish                # 写进 config.fish 的托管块（标记之间会被整段替换）
│   ├── conf.d/
│   │   ├── fishkit.fish           # 模板：FISHKIT_DIR / FISHKIT_PROMPT，安装时注入
│   │   └── proxy.fish             # proxy-on / proxy-off / proxy-status
│   ├── functions/                 # pkg、pkgview、aptui、__fishkit_dir
│   ├── completions/pkg.fish       # Tab 补全
│   └── themes/
│       ├── starship/starship.toml # 参照 NyxNiri 的提示符配置
│       └── montys/fish_prompt.fish
└── tests/                         # selftest / uitest / fishcheck
```

两种安装模式：

| 模式 | 命令 | 位置 | 删掉仓库 |
| --- | --- | --- | --- |
| **系统**（默认） | `bash install.sh` | `/usr/local/lib/fishkit`、`/usr/local/bin`、`/usr/share/fish/vendor_*` | 照常用 |
| 用户 | `bash install.sh --user` | `~/.config/fish`，`FISHKIT_DIR` 指向仓库 | 会失效 |

系统安装后更新代码：`cp` 两个 `.py` 到 `/usr/local/lib/fishkit/bin/`，或重跑 `install.sh`。

## 安装

```bash
bash install.sh                 # 一条命令搞定：装 fish + 依赖 + 提示符 + 工具
bash install.sh --user          # 只装当前用户
bash install.sh --no-starship   # 不联网下 starship，用系统级 montys
bash install.sh --no-deps       # 不自动 apt 装东西
bash install.sh --no-shell      # 不改登录 shell
bash install.sh --dry-run       # 只打印要做什么，不提权、不写文件
bash install.sh --test          # 装完跑自测
bash install.sh --uninstall     # 卸载（系统模式同样要 sudo）
```

它会把一路缺的东西补齐：

| 缺什么 | 默认动作 |
| --- | --- |
| `python3` | `apt-get install -y python3`（必需，装不上就停） |
| `fish` | `apt-get install -y fish`，然后 `chsh -s $(command -v fish) $SUDO_USER`（写进 `/etc/shells`，重新登录生效） |
| `fzf` | `apt-get install -y fzf`（`pkg se` / `pkg rm` 用） |
| `curl` | `apt-get install -y curl`（下载 starship 用） |
| `starship` | 有现成的就复制到 `/usr/local/bin`，没有就用官方脚本装；失败则退回 montys |
| 皮肤 | starship.toml 装到 `/usr/local/share/fishkit/`；montys 备用 |

| 选项 | 说明 |
| --- | --- |
| `-y, --yes` | 不询问 |
| `--system` / `--user` | 安装模式，默认 `system` |
| `--theme auto\|starship\|montys\|none` | 提示符方案，默认 `auto` |
| `--no-deps` | 不自动 apt 装依赖 |
| `--no-shell` | 不改登录 shell |
| `--no-starship` | 不下载 starship（用 montys） |
| `--starship` | 兼容旧参数（现在默认就会装） |
| `--no-proxy` | 不装 `proxy.fish` |
| `--no-config` | 不碰 `config.fish`（仅 `--user`） |
| `--links` | 在 `~/.local/bin` 建软链（仅 `--user`） |
| `-n, --dry-run` | 不写任何文件 |
| `--test` | 跑 `tests/` 里的三个自测 |
| `--uninstall` | 按当前模式卸载，备份保留 |

系统安装的落地位置：

```
/usr/local/lib/fishkit/bin/{pkgview,aptui}.py     # 工具本体
/usr/local/bin/{pkgview,aptui}                    # 软链，任何 shell 都能直接敲
/usr/local/bin/starship                           # 没有就用官方脚本装
/usr/local/share/fishkit/starship.toml            # 所有用户共用的提示符配置
/usr/share/fish/vendor_functions.d/               # pkg / pkgview / aptui / __fishkit_dir / fish_mode_prompt
/usr/share/fish/vendor_completions.d/pkg.fish     # Tab 补全
/usr/share/fish/vendor_conf.d/fishkit.fish        # FISHKIT_DIR / FISHKIT_PROMPT / STARSHIP_CONFIG
/usr/share/fish/vendor_conf.d/proxy.fish          # proxy-on / proxy-off / proxy-status
```

行为约定：

- 装之前**先备份**：系统文件 → `/usr/local/share/fishkit-backup/<时间戳>/`，
  用户文件 → `~/.config/fish/fishkit-backup/<时间戳>/`，卸载不删备份
- 可重复执行：内容没变的文件会跳过，不反复改 mtime、不堆备份
- 系统安装会把之前用户级装的那套（函数/补全/conf.d/fishkit.fish）**收走**，
  避免 `FISHKIT_DIR` 还指着仓库；`config.fish` 里的托管块也一并摘掉，交给 `vendor_conf.d`
- 卸载不删 `starship` 二进制（可能是你自己装的）

## 提示符（外观）

`config.fish` 的托管块里只有一句决定谁接管：

```fish
if test "$FISHKIT_PROMPT" = starship; and type -q starship
    starship init fish | source
end
```

`FISHKIT_PROMPT` 由 `install.sh` 写进 `conf.d/fishkit.fish`。starship 的配置在
`~/.config/starship.toml`，改颜色就改那里（`[palettes.nyx]` 一段）。

左边是「系统 + 用户 + 目录 + git + 耗时 + 代理端口 + 语言版本」，右边没有东西——
**时间不再重复显示**（左侧提示符本身没有时钟，starship 里 `[time]` 也是关的）。

对齐参考仓库的几个细节：

- `set fish_greeting` —— 新开终端不再打印欢迎语
- `fish_mode_prompt.fish` —— vi 键位下日常插入模式不再挂 `[I]`，只有 `Esc` 进普通模式
  才显示 `[N]`，可视/替换模式照旧。想恢复原样，删掉这个文件即可
- `conf.d/fishkit.fish` 里把 `~/.local/bin` 提到 PATH 最前，starship / uv / nvim 都在那儿
- 语言段（python / node / rust / c）和系统图标用的都是参考仓库原来那套 Nerd Font 字形
- 代理端口是自定义段：只在 `proxy-on` 之后出现，`proxy-off` 后整段消失
  （参考仓库里那段 `when` 在某些 shell 下会超时，这里改成了 `test -n "$http_proxy..."` 单条判断）
- 清单里没搬 `eza` 的 `ls --icons` 别名（本机没装 eza），也没搬 kitty / noctalia / niri
  那些 Arch + Wayland 桌面组件

没装 starship 时，`functions/fish_prompt.fish`（montys）生效，就是原来那套
`localhost | ~ | 2ms | 时间 | 状态` 的段式提示符。`--theme montys` 可以切回去。

## pkg 命令

```
pkg                      # 打开 TUI 浏览已安装的程序
pkg nginx                # 不开界面，按正则查已安装的包
pkg find '^libssl' snap  # 指定数据源：all / apt / snap / flatpak / pip / cmd
pkg se riscv             # fzf 搜 apt 全部包（多选安装，右侧预览详情）
pkg rm                   # fzf 搜已安装的包（多选卸载）
pkg apt                  # 内置 TUI 搜索并安装（不依赖 fzf）
pkg which ls             # 这命令是哪个包装的 + 版本
pkg help
```

`pk` = `pkg`，`pka` = `pkg apt`。`pkg <Tab>` 补子命令，`pkg which <Tab>` 补命令名。

`pkg se` / `pkg rm` 用真 fzf：`[Tab]` 多选、右侧 `--preview` 显示版本/体积/主页/描述，
最后走 `sudo apt-get install|remove`（密码提示正常）。没装 fzf 时自动退回内置 TUI。

## pkgview（已安装程序 TUI）

```bash
python3 bin/pkgview.py           # 或 pkg
python3 bin/pkgview.py -s snap   # 直接打开 snap
python3 bin/pkgview.py --dump apt -f openssl   # 非交互，给 grep 用
```

| 按键 | 作用 |
| --- | --- |
| `/` | 输入正则（实时过滤，Enter 确认，Esc 取消） |
| `j k` ↑↓ | 上下移动 |
| `g G` / `Ctrl-d Ctrl-u` | 首行末行 / 翻页 |
| `Tab` `h l` / `1`..`9` | 切换数据源 / 跳到第 N 个 |
| `s` | 排序：名称↑↓ / 体积↑↓ |
| `r` | 重读当前数据源 |
| `Esc` | 清正则；读取中按下则取消；空时退出 |
| `q` | 退出 |

正则是 Python `re`、忽略大小写，匹配名称/版本/摘要/路径/主页。数据源全部后台线程加载，
读取时界面不卡，转圈有已等时长。

## aptui（apt 包 TUI）

```bash
python3 bin/aptui.py         # 或 pkg apt
python3 bin/aptui.py -y      # 装的时候自动加 -y
python3 bin/aptui.py --dump bsh --plain   # 给 fzf/脚本用的精简输出
python3 bin/aptui.py --show nginx         # 单包详情（fzf 预览就是它）
```

| 按键 | 作用 |
| --- | --- |
| 直接打字 | 追加搜索词（fzf 式子序列匹配） |
| `Backspace` / `Ctrl-u` | 删一个 / 清空 |
| ↑↓ `Ctrl-p Ctrl-n` | 上下移动 |
| `Enter` | 安装（弹确认，按 `y` 才执行） |
| `Ctrl-x` | 卸载（需确认） |
| `Ctrl-r` | 重读 apt 缓存 |
| `Ctrl-g` | 帮助 |
| `Esc` / `Ctrl-q` | 清空 / 退出 |

`apt list` 的结果缓存在 `~/.cache/fishkit/apt-list.txt`，用 `/var/lib/apt/lists` 的 mtime
判失效；`apt update` 后自动重扫，也可 `Ctrl-r` 强制重扫。详情是后台线程查的，翻列表不卡。

## proxy.fish

`proxy-on` 两种安装模式都会装（系统模式在 `vendor_conf.d/`，用户模式在 `conf.d/`），
Win 宿主 / 局域网代理都能用：

```fish
proxy-on            # 自动探测（默认只认 10808）
proxy-on http       # 强制 http
proxy-off           # 关
proxy-status        # 状态 + 连通性
proxy-on -f         # 忽略缓存重新探测
proxy-off --forget  # 清掉缓存的端点
```

首次探测成功会把端点记进 `PROXY_CACHED`，之后只做一次 TCP 探测；
`set -Ux PROXY_NO_CHECK 1` 可完全跳过探测。端口用 `PROXY_PORT` 改。

## 自测

```bash
python3 tests/selftest.py    # 52 项：解析、正则、模糊打分、缓存、取消、curses 常量守卫
python3 tests/uitest.py      # 离屏渲染 10 个界面，检查不抛异常、不越界
python3 tests/fishcheck.py fish/functions/*.fish fish/conf.d/*.fish   # 块结构 + bash 语法残留
```

`--test` 会把这三个跑一遍。两条静态守卫，都是被真机事故逼出来的：

- `selftest`：源码里出现的 `curses.XXX` 必须真实存在（防 `curses.A_NONE` 那种只在真机上炸的错）
- `fishcheck`：除块结构配平外，还查 `${var}`、`$(cmd)`、`[[ ]]` 这些 **fish 不认的 bash 写法**
  （单引号里的内容不算，比如 `dpkg-query -f '${Version}'` 是合法的）。`fish -n` 能查出来，
  但本机没有 fish 时就只能靠它。

## 排障

| 现象 | 原因 / 处理 |
| --- | --- |
| `pkg: 找不到 ...` | `FISHKIT_DIR` 失效（仓库挪过位置），重跑 `install.sh` |
| `pkg` 报 fish 语法错 | 跑 `fish -n ~/.config/fish/functions/pkg.fish`，把行号贴出来 |
| 切到「命令」页很慢 | 会自动跳过 `/mnt/*`、`/proc`、`/sys`；要包含 Windows 的 exe 用 `PKGVIEW_CMD_WSL=1 pkg` |
| `pkg se` 不出现 fzf | 没装 fzf，`sudo apt install fzf`，或直接用 `pkg apt` |
| 提示符没变 | `exec fish`；仍是旧的就看 `echo $FISHKIT_PROMPT` 和 `command -v starship` |
