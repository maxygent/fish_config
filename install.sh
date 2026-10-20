#!/usr/bin/env bash
# =============================================================================
# fishkit 安装器
#
#   bash install.sh              默认【系统安装】：装进 /usr/local 和 /usr/share/fish
#                                文件是实体副本，之后删掉 fishkit 仓库也不影响使用
#   bash install.sh --user       只装当前用户（~/.config/fish，仓库必须在）
#   bash install.sh --uninstall  卸载（按当前模式；系统安装需要 sudo）
#   bash install.sh --dry-run    只看会做什么
#   bash install.sh --test       装完跑自测
#
# 系统安装会要 sudo（脚本自己 exec sudo，你只需要一条命令）。
# =============================================================================
set -euo pipefail

VERSION="2.0"

SRC=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ORIG_ARGS=("$@")

# 用户级位置
XDG_CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
FISH_CFG="$XDG_CFG/fish"
OMF_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/omf"
BIN_LINK_DIR="$HOME/.local/bin"

# 系统级位置
SYS_PREFIX="${FISHKIT_PREFIX:-/usr/local}"
SYS_LIB="$SYS_PREFIX/lib/fishkit"
SYS_SHARE="$SYS_PREFIX/share/fishkit"
FISH_DATA="${FISH_DATA:-/usr/share/fish}"

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="$FISH_CFG/fishkit-backup/$STAMP"
SYS_BACKUP="$SYS_PREFIX/share/fishkit-backup/$STAMP"

MARK_BEGIN="# >>> fishkit >>>"
MARK_END="# <<< fishkit <<<"

MODE="system"          # system | user
THEME="auto"           # auto | starship | montys | none
DO_DEPS=1              # 缺什么 apt 装什么（fish/python3/fzf/curl）
DO_SHELL=1             # 把 fish 设成登录 shell
DO_STARSHIP=1          # 没有 starship 就下载
DO_PROXY=1
DO_CONFIG=1
DO_LINKS=0
DRY=0
UNINSTALL=0
RUN_TESTS=0
ASSUME_YES=0
BACKED_UP=0

# ---------------------------------------------------------------- 输出与工具 --
if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'
    C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_OK=""; C_WARN=""; C_ERR=""; C_DIM=""; C_OFF=""
fi

log()  { printf '  %s\n' "$*"; }
dim()  { printf '  %s%s%s\n' "$C_DIM" "$*" "$C_OFF"; }
ok()   { printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
die()  { printf '%sx%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }
title(){ printf '\n%s%s%s\n' $'\033[1m' "$*" "$C_OFF"; }

usage() {
    cat <<EOF
fishkit $VERSION —— fish 配置 + pkgview / aptui

用法: bash install.sh [选项]

  默认系统安装：复制到 /usr/local 与 /usr/share/fish，删掉本仓库也能照常用。

  -y, --yes              不问，直接装
      --user             只装当前用户（~/.config/fish，依赖本仓库目录）
      --system           强制系统安装（默认）
      --theme <名>       提示符: auto（默认）/ starship / montys / none
      --no-deps          不自动 apt 装 fish/python3/fzf/curl
      --no-shell         不改登录 shell
      --no-starship      不下载 starship，直接用 montys
      --starship         兼容旧参数（现在默认就会装）
      --no-proxy         不装 proxy-on / proxy-off
      --no-config        不碰 config.fish（仅 --user）
      --links            额外在 ~/.local/bin 建软链（仅 --user）
  -n, --dry-run          只打印将要做什么，不写任何文件
      --test             装完跑自测
      --uninstall        卸载（系统安装同样需要 sudo）
  -h, --help             这份帮助

系统安装布局:
  工具     $SYS_LIB/bin/{pkgview,aptui}.py，软链到 $SYS_PREFIX/bin
  函数     $FISH_DATA/vendor_functions.d/
  补全     $FISH_DATA/vendor_completions.d/pkg.fish
  环境     $FISH_DATA/vendor_conf.d/fishkit.fish
  代理     $FISH_DATA/vendor_conf.d/proxy.fish
  提示符   $SYS_SHARE/starship.toml（所有用户共用）
  备份     $SYS_BACKUP

用户安装布局:
  函数/补全/conf.d   $FISH_CFG/
  提示符             $XDG_CFG/starship.toml 或 $OMF_DIR/themes/montys
  备份               $BACKUP_DIR
EOF
}

# 非 tty（curl | bash、CI）视为 -y，避免卡在 read 上
confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    [ -t 0 ] || return 0
    printf '  %s [Y/n] ' "$1"
    read -r a || return 0
    case "${a:-y}" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# 路径里的 | & \ 不能直接塞进 sed 的替换串
sed_escape() { printf '%s' "$1" | sed 's/[&\\|]/\\&/g'; }

# 备份到指定目录（同名文件按 basename 存，够用）
backup_into() {
    local dir=$1 f=$2 base
    [ -e "$f" ] || [ -L "$f" ] || return 0
    [ "$DRY" = 1 ] && return 0
    base=$(basename "$f")
    mkdir -p "$dir"
    cp -a "$f" "$dir/$base"
    BACKED_UP=$((BACKED_UP + 1))
}

# 用户级：备份到 $BACKUP_DIR，保留 functions/ 这类子目录
backup_user() {
    local f=$1 rel
    [ -e "$f" ] || [ -L "$f" ] || return 0
    rel=${f#"$FISH_CFG"/}
    [ "$rel" = "$f" ] && rel=$(basename "$f")
    [ "$DRY" = 1 ] && return 0
    mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
    cp -a "$f" "$BACKUP_DIR/$rel"
    BACKED_UP=$((BACKED_UP + 1))
}

# 用户级复制：内容没变就跳过，变了先备份、并去掉 CRLF
install_file() {
    local src=$1 dest=$2
    if [ "$DRY" = 1 ]; then
        printf '  %s[dry]%s %s\n' "$C_DIM" "$C_OFF" "${dest#$FISH_CFG/}"
        return 0
    fi
    if cmp -s "$src" "$dest" 2>/dev/null; then
        printf '  %s·%s %s 没变化\n' "$C_DIM" "$C_OFF" "${dest#$FISH_CFG/}"
        return 0
    fi
    mkdir -p "$(dirname "$dest")"
    backup_user "$dest"
    cp -f "$src" "$dest"
    if grep -q $'\r' "$dest" 2>/dev/null; then
        sed -i 's/\r$//' "$dest"
    fi
    printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "${dest#$FISH_CFG/}"
}

# 系统级复制
install_sys_file() {
    local src=$1 dest=$2 mode=${3:-644}
    if [ "$DRY" = 1 ]; then
        printf '  %s[dry]%s %s\n' "$C_DIM" "$C_OFF" "$dest"
        return 0
    fi
    if cmp -s "$src" "$dest" 2>/dev/null; then
        printf '  %s·%s %s 没变化\n' "$C_DIM" "$C_OFF" "$dest"
        return 0
    fi
    mkdir -p "$(dirname "$dest")"
    [ -e "$dest" ] && backup_into "$SYS_BACKUP" "$dest"
    cp -f "$src" "$dest"
    chmod "$mode" "$dest"
    printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$dest"
}

# 删文件前先备份（用户级）
remove_user_file() {
    local f=$1
    [ -e "$f" ] || [ -L "$f" ] || return 0
    if [ "$DRY" = 1 ]; then
        dim "[dry] 移除 ${f#"$FISH_CFG"/}"
        return 0
    fi
    backup_user "$f"
    rm -f "$f"
    ok "移除 ${f#"$FISH_CFG"/}"
}

# config.fish 里摘掉托管块（不删用户自己的内容）
strip_config_block() {
    local cfg=$1 tmp
    [ -f "$cfg" ] || return 0
    grep -qF "$MARK_BEGIN" "$cfg" || return 0
    if [ "$DRY" = 1 ]; then
        dim "[dry] 从 config.fish 摘掉托管块"
        return 0
    fi
    tmp="$cfg.fishkit-tmp"
    awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
        index($0,b)==1 {skip=1; next}
        index($0,e)==1 {skip=0; next}
        skip {next}
        {n++; line[n]=$0}
        END {while (n>0 && line[n] ~ /^[[:space:]]*$/) n--
             for (i=1;i<=n;i++) print line[i]}
    ' "$cfg" > "$tmp"
    backup_user "$cfg"
    mv "$tmp" "$cfg"
    ok "config.fish 摘掉 fishkit 托管块"
}

# 找到某个用户的家目录
user_home() {
    local u=${1:-}
    [ -n "$u" ] || return 1
    getent passwd "$u" 2>/dev/null | cut -d: -f6
}

# --------------------------------------------------------------------- 参数 --
while [ $# -gt 0 ]; do
    case "$1" in
        -y|--yes)      ASSUME_YES=1 ;;
        --user)        MODE="user" ;;
        --system)      MODE="system" ;;
        --theme)       shift; [ $# -gt 0 ] || die "--theme 需要一个值"; THEME=$1 ;;
        --theme=*)     THEME=${1#*=} ;;
        --starship)    DO_STARSHIP=1 ;;
        --no-starship) DO_STARSHIP=0 ;;
        --no-deps)     DO_DEPS=0 ;;
        --no-shell)    DO_SHELL=0 ;;
        --no-proxy)    DO_PROXY=0 ;;
        --no-config)   DO_CONFIG=0 ;;
        --links)       DO_LINKS=1 ;;
        -n|--dry-run)  DRY=1 ;;
        --test)        RUN_TESTS=1 ;;
        --uninstall)   UNINSTALL=1 ;;
        -h|--help)     usage; exit 0 ;;
        *)             usage >&2; die "未知参数: $1" ;;
    esac
    shift
done

case "$THEME" in
    auto|starship|montys|none) ;;
    *) die "--theme 只能是 auto / starship / montys / none" ;;
esac

# -------------------------------------------------------------------- 依赖 --
apt_install() {
    local pkg=$1
    command -v apt-get >/dev/null 2>&1 || return 1
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$pkg" >/dev/null 2>&1 && return 0
    DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$pkg" >/dev/null 2>&1
}

# 只有「真·系统安装 + root + 非 dry-run」才允许装包
CAN_BOOTSTRAP=0
if [ "$DRY" = 0 ] && [ "$(id -u)" -eq 0 ] && [ "$SYS_PREFIX" = "/usr/local" ]; then
    CAN_BOOTSTRAP=1
fi

ensure_cmd() {   # ensure_cmd <命令> <apt 包> [必需=0]
    local cmd=$1 pkg=$2 need=${3:-0}
    command -v "$cmd" >/dev/null 2>&1 && return 0
    if [ "$DRY" = 1 ]; then
        dim "[dry] 缺 $cmd，会 apt-get install -y $pkg"
        return 0
    fi
    if [ "$DO_DEPS" = 0 ] || [ "$CAN_BOOTSTRAP" = 0 ]; then
        if [ "$need" = 1 ]; then die "缺 $cmd，先装：sudo apt-get install -y $pkg"; fi
        warn "缺 $cmd（没自动装）"
        return 0
    fi
    log "安装依赖 $pkg …"
    if apt_install "$pkg"; then
        ok "$cmd 已就绪"
    else
        if [ "$need" = 1 ]; then die "装不上 $pkg：sudo apt-get install -y $pkg"; fi
        warn "装不上 $pkg，相关功能会缺"
    fi
    return 0
}

# 系统模式：没 root 就自己提权（dry-run 不提权）
if [ "$MODE" = system ] && [ "$SYS_PREFIX" = "/usr/local" ]; then
    if [ "$DRY" = 0 ] && [ "$(id -u)" -ne 0 ]; then
        command -v sudo >/dev/null 2>&1 \
            || die "系统安装需要 root：sudo bash $SRC/install.sh $*"
        printf '%s系统安装需要 sudo，正在提权 …%s\n' "$C_DIM" "$C_OFF"
        exec sudo -E bash "$SRC/install.sh" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
    fi
fi

# 系统模式下，动的是「调用者」的 fish 配置，不是 root 的
if [ "$MODE" = system ] && [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    CALLER_HOME=$(user_home "$SUDO_USER" || true)
    if [ -n "${CALLER_HOME:-}" ] && [ -d "$CALLER_HOME" ]; then
        HOME="$CALLER_HOME"
        FISH_CFG="$CALLER_HOME/.config/fish"
        XDG_CFG="$CALLER_HOME/.config"
        OMF_DIR="$CALLER_HOME/.local/share/omf"
    fi
fi
BACKUP_DIR="$FISH_CFG/fishkit-backup/$STAMP"

# 把 fish 也一起装好、设成登录 shell
if [ "$MODE" = system ]; then
    title "系统依赖"
    ensure_cmd python3 python3 1
    ensure_cmd fish fish 0
    ensure_cmd fzf fzf 0
    ensure_cmd curl curl 0
else
    command -v python3 >/dev/null 2>&1 \
        || die "需要 python3：sudo apt-get install -y python3"
fi

HAVE_FISH=0
command -v fish >/dev/null 2>&1 && HAVE_FISH=1

if [ "$MODE" = system ] && [ "$DO_SHELL" = 1 ] && [ "$HAVE_FISH" = 1 ]; then
    if [ "$CAN_BOOTSTRAP" = 1 ]; then
        target_user=${SUDO_USER:-root}
        fish_path=$(command -v fish)
        grep -qxF "$fish_path" /etc/shells 2>/dev/null || echo "$fish_path" >> /etc/shells
        cur_shell=$(getent passwd "$target_user" 2>/dev/null | cut -d: -f7)
        if [ "$cur_shell" != "$fish_path" ]; then
            if chsh -s "$fish_path" "$target_user" >/dev/null 2>&1; then
                ok "登录 shell: ${cur_shell:-?} → $fish_path（重新登录生效）"
            else
                warn "chsh 失败，手动改：chsh -s $fish_path"
            fi
        else
            dim "登录 shell 已经是 $fish_path"
        fi
    else
        dim "dry-run / 非系统前缀：跳过改登录 shell"
    fi
fi

# ------------------------------------------------------------------ 主题决策 --
# auto：能装 starship 就用它，否则退回 montys
has_starship() {
    command -v starship >/dev/null 2>&1 && return 0
    [ -f "$SYS_PREFIX/bin/starship" ] && return 0
    [ -x "$HOME/.local/bin/starship" ] && return 0
    return 1
}
THEME_REQ=$THEME      # 用户原始选择，日志里用
if [ "$THEME" = auto ]; then
    if has_starship || [ "$DO_STARSHIP" = 1 ]; then THEME=starship; else THEME=montys; fi
fi
if [ "$THEME" = none ]; then PROMPT_VAL=""; else PROMPT_VAL=$THEME; fi

title "fishkit $VERSION"
log "仓库      $SRC"
if [ "$MODE" = system ]; then
    log "模式      系统安装（$SYS_PREFIX + $FISH_DATA）"
    case "$THEME_REQ" in
        none)   log "提示符    不接管" ;;
        montys) log "提示符    montys（系统级 fish_prompt）" ;;
        *)
            if [ "$DO_STARSHIP" = 1 ]; then
                log "提示符    starship（装不上会退回 montys）"
            else
                log "提示符    montys（--no-starship）"
            fi ;;
    esac
else
    log "模式      用户安装（$FISH_CFG）"
    log "提示符    $THEME"
fi
[ "$HAVE_FISH" = 1 ] || warn "没检测到 fish：文件照样装好，但需要先装 fish 才会生效"
if [ "$DRY" = 1 ]; then warn "dry-run：只打印，不写任何文件"; fi

# ------------------------------------------------------------------- 卸载分支 --
if [ "$UNINSTALL" = 1 ]; then
    title "卸载"
    confirm "确认卸载 fishkit（$MODE 模式）？" || die "已取消"

    if [ "$MODE" = system ]; then
        for f in "$FISH_DATA"/vendor_functions.d/{pkg,pkgview,aptui,__fishkit_dir,fish_mode_prompt}.fish \
                 "$FISH_DATA"/vendor_completions.d/pkg.fish \
                 "$FISH_DATA"/vendor_conf.d/fishkit.fish \
                 "$FISH_DATA"/vendor_conf.d/proxy.fish; do
            [ -e "$f" ] || continue
            if [ "$DRY" = 1 ]; then dim "[dry] 删除 $f"
            else rm -f "$f"; ok "删除 $f"; fi
        done
        for f in "$SYS_PREFIX"/bin/pkgview "$SYS_PREFIX"/bin/aptui; do
            if [ -L "$f" ]; then
                if [ "$DRY" = 1 ]; then dim "[dry] 删除软链 $f"
                else rm -f "$f"; ok "删除软链 $f"; fi
            fi
        done
        if [ "$DRY" = 1 ]; then dim "[dry] 删除 $SYS_LIB 与 $SYS_SHARE"
        else rm -rf "$SYS_LIB" "$SYS_SHARE"; ok "删除 $SYS_LIB、$SYS_SHARE"; fi
        dim "starship 二进制没动（可能你自己装的）：$SYS_PREFIX/bin/starship"
        title "卸载完成"
        exit 0
    fi

    for f in functions/pkg.fish functions/pkgview.fish functions/aptui.fish \
             functions/__fishkit_dir.fish functions/fish_mode_prompt.fish \
             completions/pkg.fish \
             conf.d/fishkit.fish conf.d/proxy.fish; do
        [ -e "$FISH_CFG/$f" ] || continue
        if [ "$DRY" = 1 ]; then dim "[dry] 删除 $f"
        else rm -f "$FISH_CFG/$f"; ok "删除 $f"; fi
    done

    if [ -f "$OMF_DIR/themes/montys/fish_prompt.fish" ] \
       && cmp -s "$SRC/fish/themes/montys/fish_prompt.fish" \
                 "$OMF_DIR/themes/montys/fish_prompt.fish"; then
        if [ "$DRY" = 1 ]; then dim "[dry] 删除 omf 主题 montys"
        else rm -rf "$OMF_DIR/themes/montys"; ok "删除 omf 主题 montys"; fi
    fi
    if [ -L "$FISH_CFG/functions/fish_prompt.fish" ]; then
        if [ "$DRY" = 1 ]; then dim "[dry] 断开 fish_prompt 软链"
        else rm -f "$FISH_CFG/functions/fish_prompt.fish"; ok "断开 fish_prompt 软链"; fi
    fi
    if [ -f "$XDG_CFG/starship.toml" ] \
       && cmp -s "$SRC/fish/themes/starship/starship.toml" "$XDG_CFG/starship.toml"; then
        if [ "$DRY" = 1 ]; then dim "[dry] 删除 starship.toml"
        else rm -f "$XDG_CFG/starship.toml"; ok "删除 starship.toml"; fi
    fi
    strip_config_block "$FISH_CFG/config.fish"

    [ "$DRY" = 1 ] || { [ -d "$BACKUP_DIR" ] && log "备份: $BACKUP_DIR"; }
    title "卸载完成"
    dim "新开一个 fish 生效。想彻底恢复，从 $BACKUP_DIR 里拷回对应文件。"
    exit 0
fi

# =========================================================== 系统安装 =========
if [ "$MODE" = system ]; then
    confirm "开始系统安装到 $SYS_PREFIX ？" || die "已取消"

    title "工具 → $SYS_LIB/bin"
    install_sys_file "$SRC/bin/pkgview.py" "$SYS_LIB/bin/pkgview.py" 755
    install_sys_file "$SRC/bin/aptui.py"   "$SYS_LIB/bin/aptui.py"   755
    if [ "$DRY" = 0 ]; then
        mkdir -p "$SYS_PREFIX/bin"
        ln -sfn "$SYS_LIB/bin/pkgview.py" "$SYS_PREFIX/bin/pkgview"
        ln -sfn "$SYS_LIB/bin/aptui.py"   "$SYS_PREFIX/bin/aptui"
    fi
    ok "$SYS_PREFIX/bin/{pkgview,aptui} → $SYS_LIB/bin"

    title "fish 函数与补全 → $FISH_DATA/vendor_*"
    for f in "$SRC"/fish/functions/*.fish; do
        install_sys_file "$f" "$FISH_DATA/vendor_functions.d/$(basename "$f")"
    done
    install_sys_file "$SRC/fish/completions/pkg.fish" \
                     "$FISH_DATA/vendor_completions.d/pkg.fish"

    if [ "$DO_PROXY" = 1 ]; then
        install_sys_file "$SRC/fish/conf.d/proxy.fish" \
                         "$FISH_DATA/vendor_conf.d/proxy.fish"
    else
        dim "跳过 proxy-on / proxy-off（--no-proxy）"
    fi

    title "提示符素材 → $SYS_SHARE"
    install_sys_file "$SRC/fish/themes/starship/starship.toml" \
                     "$SYS_SHARE/starship.toml"
    install_sys_file "$SRC/fish/themes/montys/fish_prompt.fish" \
                     "$SYS_SHARE/themes/montys/fish_prompt.fish"

    # starship 二进制：优先搬现成的，没有就下载（--no-starship 可关）
    if [ ! -f "$SYS_PREFIX/bin/starship" ]; then
        srcbin=""
        for c in "$HOME/.local/bin/starship" /usr/bin/starship; do
            [ -f "$c" ] && { srcbin=$c; break; }
        done
        if [ -n "$srcbin" ]; then
            if [ "$DRY" = 1 ]; then dim "[dry] $srcbin → $SYS_PREFIX/bin/starship"
            elif cmp -s "$srcbin" "$SYS_PREFIX/bin/starship" 2>/dev/null; then
                dim "$SYS_PREFIX/bin/starship 没变化"
            else cp -f "$srcbin" "$SYS_PREFIX/bin/starship"; chmod 755 "$SYS_PREFIX/bin/starship"
                 ok "starship ← $srcbin"; fi
        elif [ "$DO_STARSHIP" = 1 ]; then
            if [ "$DRY" = 1 ]; then
                dim "[dry] 下载 starship → $SYS_PREFIX/bin"
            elif command -v curl >/dev/null 2>&1; then
                if curl -fsSL https://starship.rs/install.sh \
                        | sh -s -- -y --bin-dir "$SYS_PREFIX/bin" >/dev/null 2>&1; then
                    chmod 755 "$SYS_PREFIX/bin/starship"
                    ok "starship → $SYS_PREFIX/bin/starship"
                else
                    warn "starship 下载失败（网络？），先用 montys 顶上"
                fi
            else
                warn "没有 curl，跳过 starship 下载"
            fi
        fi
    else
        dim "$SYS_PREFIX/bin/starship 已存在"
    fi

    # 最终决定用哪套提示符
    if [ "$THEME" = none ]; then
        PROMPT_VAL=""
        dim "提示符：不接管"
    elif [ -f "$SYS_PREFIX/bin/starship" ] && [ "$THEME" != montys ]; then
        PROMPT_VAL=starship
        ok "提示符：starship"
    else
        PROMPT_VAL=montys
        install_sys_file "$SRC/fish/themes/montys/fish_prompt.fish" \
                         "$FISH_DATA/vendor_functions.d/fish_prompt.fish"
        ok "提示符：montys（系统级 fish_prompt）"
    fi

    # 所有用户共用的环境变量 + 提示符初始化
    title "vendor_conf.d/fishkit.fish"
    if [ "$DRY" = 1 ]; then
        dim "[dry] $FISH_DATA/vendor_conf.d/fishkit.fish"
    else
        gen="$FISH_DATA/vendor_conf.d/.fishkit.fish.tmp"
        mkdir -p "$FISH_DATA/vendor_conf.d"
        {
            echo '# 由 fishkit install.sh 生成（系统安装），重跑会整体覆盖'
            echo "set -g FISHKIT_DIR '$SYS_LIB'"
            echo "set -g FISHKIT_PROMPT '$PROMPT_VAL'"
            echo "set -gx STARSHIP_CONFIG '$SYS_SHARE/starship.toml'"
            echo ''
            echo 'if test -d $HOME/.local/bin'
            echo '    fish_add_path -g -m $HOME/.local/bin'
            echo 'end'
            echo ''
            echo 'if status is-interactive'
            echo '    abbr --add pk pkg'
            echo "    abbr --add pka 'pkg apt'"
            echo 'end'
            echo ''
            # 托管块里的实质内容（去掉标记和说明）
            grep -v -e '^# >>> fishkit >>>' -e '^# <<< fishkit <<<' \
                    -e '^# 这一段由 fishkit' -e '^# 要调整就改 fishkit' \
                    -e '具体用哪套' \
                    "$SRC/fish/config.fish"
        } > "$gen"
        if cmp -s "$gen" "$FISH_DATA/vendor_conf.d/fishkit.fish"; then
            dim "vendor_conf.d/fishkit.fish 没变化"
            rm -f "$gen"
        else
            [ -e "$FISH_DATA/vendor_conf.d/fishkit.fish" ] \
                && backup_into "$SYS_BACKUP" "$FISH_DATA/vendor_conf.d/fishkit.fish"
            mv "$gen" "$FISH_DATA/vendor_conf.d/fishkit.fish"
            chmod 644 "$FISH_DATA/vendor_conf.d/fishkit.fish"
            ok "$FISH_DATA/vendor_conf.d/fishkit.fish"
        fi
    fi

    # 清掉之前用户级装的那份，否则 FISHKIT_DIR 还指着仓库
    title "清理用户级旧文件（避免指回仓库）"
    cleaned=0
    for f in conf.d/fishkit.fish completions/pkg.fish \
             functions/pkg.fish functions/pkgview.fish functions/aptui.fish \
             functions/__fishkit_dir.fish functions/fish_mode_prompt.fish; do
        if [ -f "$FISH_CFG/$f" ]; then
            remove_user_file "$FISH_CFG/$f"
            cleaned=1
        fi
    done
    if [ "$cleaned" = 0 ]; then dim "没有需要清理的用户级文件"; fi
    if [ -f "$XDG_CFG/starship.toml" ] \
       && cmp -s "$SRC/fish/themes/starship/starship.toml" "$XDG_CFG/starship.toml"; then
        remove_user_file "$XDG_CFG/starship.toml"
    fi
    # config.fish 里的托管块由 vendor_conf 接管
    strip_config_block "$FISH_CFG/config.fish"

    title "校验"
    if [ "$DRY" = 0 ]; then
        for f in "$SRC"/bin/*.py; do
            python3 -c 'import ast,sys; ast.parse(sys.stdin.read(), sys.argv[1])' "$(basename "$f")" < "$f" \
                || die "python 语法错误: $f"
        done
        ok "python 语法 OK"
        if [ "$HAVE_FISH" = 1 ]; then
            for f in "$SRC"/fish/functions/*.fish "$SRC"/fish/completions/*.fish; do
                base=$(basename "$f")
                case "$base" in
                    pkg.fish|pkgview.fish|aptui.fish|__fishkit_dir.fish|fish_mode_prompt.fish)
                        fish -n "$FISH_DATA/vendor_functions.d/$base" || die "语法错误: $base" ;;
                esac
            done
            fish -n "$FISH_DATA/vendor_completions.d/pkg.fish" || die "语法错误: pkg.fish（补全）"
            fish -n "$FISH_DATA/vendor_conf.d/fishkit.fish" || die "语法错误: vendor_conf"
            if [ -f "$FISH_DATA/vendor_conf.d/proxy.fish" ]; then
                fish -n "$FISH_DATA/vendor_conf.d/proxy.fish" || die "语法错误: proxy.fish"
            fi
            ok "fish -n 语法检查通过"
        fi
        [ -x "$SYS_PREFIX/bin/pkgview" ] && ok "pkgview 可直接执行"
    fi

    if [ "$RUN_TESTS" = 1 ] && [ "$DRY" = 0 ]; then
        title "自测"
        ( cd "$SRC" && python3 tests/selftest.py | tail -3 )
        ( cd "$SRC" && python3 tests/uitest.py >/dev/null && echo "  离屏渲染 OK" )
        ( cd "$SRC" && python3 tests/fishcheck.py fish/functions/*.fish \
            fish/completions/*.fish fish/conf.d/*.fish fish/config.fish \
            fish/themes/montys/fish_prompt.fish )
    fi

    title "完成"
    [ "$BACKED_UP" -gt 0 ] && dim "备份了 $BACKED_UP 个旧文件 → $SYS_BACKUP 或 $BACKUP_DIR"
    dim "删掉 $SRC 也不影响：函数、工具、配置都已复制到系统目录"
    dim "新开一个 fish 生效。验证：pkg help / command -v pkgview"
    exit 0
fi

# =========================================================== 用户安装 =========
# 旧版本清理
if [ -f "$FISH_CFG/conf.d/termtools.fish" ]; then
    title "清理旧版本"
    backup_user "$FISH_CFG/conf.d/termtools.fish"
    [ "$DRY" = 1 ] || rm -f "$FISH_CFG/conf.d/termtools.fish"
    ok "移除 conf.d/termtools.fish（旧入口）"
fi

confirm "开始安装到 $FISH_CFG ？" || die "已取消"
[ "$DRY" = 1 ] || mkdir -p "$FISH_CFG"/{functions,completions,conf.d}

title "fish 函数与补全"
for f in "$SRC"/fish/functions/*.fish; do
    install_file "$f" "$FISH_CFG/functions/$(basename "$f")"
done
for f in "$SRC"/fish/completions/*.fish; do
    install_file "$f" "$FISH_CFG/completions/$(basename "$f")"
done

title "conf.d"
if [ "$DRY" = 1 ]; then
    dim "[dry] conf.d/fishkit.fish（FISHKIT_DIR=$SRC, PROMPT=$PROMPT_VAL）"
else
    gen="$FISH_CFG/conf.d/.fishkit.fish.tmp"
    sed -e "s|@FISHKIT_DIR@|$(sed_escape "$SRC")|g" \
        -e "s|@FISHKIT_PROMPT@|$PROMPT_VAL|g" \
        "$SRC/fish/conf.d/fishkit.fish" > "$gen"
    if cmp -s "$gen" "$FISH_CFG/conf.d/fishkit.fish"; then
        dim "conf.d/fishkit.fish 没变化"
        rm -f "$gen"
    else
        backup_user "$FISH_CFG/conf.d/fishkit.fish"
        mv "$gen" "$FISH_CFG/conf.d/fishkit.fish"
        ok "conf.d/fishkit.fish  FISHKIT_DIR=$SRC · PROMPT=$PROMPT_VAL"
    fi
fi

if [ "$DO_PROXY" = 1 ]; then
    install_file "$SRC/fish/conf.d/proxy.fish" "$FISH_CFG/conf.d/proxy.fish"
else
    dim "跳过 conf.d/proxy.fish（--no-proxy）"
fi

title "提示符 $THEME"
if [ "$THEME" = starship ]; then
    dest="$XDG_CFG/starship.toml"
    if [ "$DRY" = 1 ]; then dim "[dry] starship.toml → $dest"
    else
        mkdir -p "$(dirname "$dest")"
        if cmp -s "$SRC/fish/themes/starship/starship.toml" "$dest" 2>/dev/null; then
            dim "starship.toml 没变化"
        else
            [ -e "$dest" ] && backup_into "$BACKUP_DIR" "$dest"
            cp -f "$SRC/fish/themes/starship/starship.toml" "$dest"
            ok "starship.toml → $dest"
        fi
    fi
elif [ "$THEME" = montys ]; then
    src="$SRC/fish/themes/montys/fish_prompt.fish"
    if [ -d "$OMF_DIR/themes" ]; then
        dim "检测到 oh-my-fish，按 omf 的方式安装"
        if [ "$DRY" = 0 ]; then
            mkdir -p "$OMF_DIR/themes/montys"
            cp -f "$src" "$OMF_DIR/themes/montys/fish_prompt.fish"
            install_file "$src" "$FISH_CFG/functions/fish_prompt.fish"
            printf 'montys\n' > "$FISH_CFG/theme"
        fi
        ok "omf 主题 montys"
    else
        install_file "$src" "$FISH_CFG/functions/fish_prompt.fish"
    fi
else
    dim "跳过提示符（--theme none）"
fi

if [ "$DO_CONFIG" = 1 ]; then
    title "config.fish 托管块"
    CFG="$FISH_CFG/config.fish"
    if [ "$DRY" = 1 ]; then
        dim "[dry] 写入（或替换）$MARK_BEGIN … $MARK_END"
    else
        tmp="$CFG.fishkit-tmp"
        if [ -f "$CFG" ]; then
            awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
                index($0,b)==1 {skip=1; next}
                index($0,e)==1 {skip=0; next}
                skip {next}
                {n++; line[n]=$0}
                END {while (n>0 && line[n] ~ /^[[:space:]]*$/) n--
                     for (i=1;i<=n;i++) print line[i]}
            ' "$CFG" > "$tmp"
        fi
        [ -s "$tmp" ] && printf '\n' >> "$tmp"
        cat "$SRC/fish/config.fish" >> "$tmp"
        if cmp -s "$tmp" "$CFG"; then
            dim "config.fish 没变化"
            rm -f "$tmp"
        else
            [ -f "$CFG" ] && backup_user "$CFG"
            mv "$tmp" "$CFG"
            ok "config.fish（托管块已写入）"
        fi
    fi
else
    dim "跳过 config.fish（--no-config）"
fi

if [ "$DO_LINKS" = 1 ]; then
    title "~/.local/bin 软链"
    if [ "$DRY" = 0 ]; then
        mkdir -p "$BIN_LINK_DIR"
        ln -sfn "$SRC/bin/pkgview.py" "$BIN_LINK_DIR/pkgview"
        ln -sfn "$SRC/bin/aptui.py"   "$BIN_LINK_DIR/aptui"
        chmod +x "$SRC/bin/pkgview.py" "$SRC/bin/aptui.py"
    fi
    ok "pkgview / aptui → ~/.local/bin"
fi

title "校验"
if [ "$DRY" = 0 ]; then
    for f in "$SRC"/bin/*.py; do
        python3 -c 'import ast,sys; ast.parse(sys.stdin.read(), sys.argv[1])' "$(basename "$f")" < "$f" \
            || die "python 语法错误: $f"
    done
    ok "python 语法 OK"
    if [ "$HAVE_FISH" = 1 ]; then
        for f in "$SRC"/fish/functions/*.fish "$SRC"/fish/completions/*.fish; do
            fish -n "$FISH_CFG/${f#"$SRC"/fish/}" || die "语法错误: ${f#"$SRC"/fish/}"
        done
        fish -n "$FISH_CFG/conf.d/fishkit.fish" || die "语法错误: conf.d/fishkit.fish"
        if [ -f "$FISH_CFG/conf.d/proxy.fish" ]; then
            fish -n "$FISH_CFG/conf.d/proxy.fish" || die "语法错误: conf.d/proxy.fish"
        fi
        if [ -f "$FISH_CFG/config.fish" ]; then
            fish -n "$FISH_CFG/config.fish" || warn "config.fish 有语法错误，fish 会启动失败"
        fi
        ok "fish -n 语法检查通过"
    fi
fi

if [ "$RUN_TESTS" = 1 ] && [ "$DRY" = 0 ]; then
    title "自测"
    ( cd "$SRC" && python3 tests/selftest.py | tail -3 )
    ( cd "$SRC" && python3 tests/uitest.py >/dev/null && echo "  离屏渲染 OK" )
    ( cd "$SRC" && python3 tests/fishcheck.py fish/functions/*.fish \
        fish/completions/*.fish fish/conf.d/*.fish fish/config.fish \
        fish/themes/montys/fish_prompt.fish )
fi

title "完成"
[ "$BACKED_UP" -gt 0 ] && dim "备份了 $BACKED_UP 个旧文件 → $BACKUP_DIR"
dim "提示符: $THEME"
dim "注意：用户安装依赖 $SRC 这个目录，删了 pkg 就不能用了"
dim "想要不依赖仓库，跑 bash $SRC/install.sh（默认系统安装）"
dim "新开一个 fish（或 exec fish）生效"
