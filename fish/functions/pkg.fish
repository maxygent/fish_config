function __pkg_usage --description 'pkg 的帮助'
    echo '用法: pkg [子命令|正则] [参数]'
    echo ''
    echo '  pkg                     打开 TUI，浏览已安装的程序'
    echo '                          (apt / snap / flatpak / pip / PATH 命令，Tab 切换)'
    echo '  pkg <正则>              直接查已安装的包，例: pkg "^libssl|nginx"'
    echo '  pkg find <正则> [源]    同上，可指定源: all apt snap flatpak pip cmd'
    echo '  pkg se [关键词]         fzf 搜 apt 全部包，多选安装（装了 fzf 才走这条）'
    echo '  pkg rm [关键词]         fzf 搜已安装的包，多选卸载'
    echo '  pkg apt [关键词]        搜索 apt 缓存里的全部包并安装（内置 TUI，不依赖 fzf）'
    echo '  pkg which <命令>        这个命令是哪个包装的'
    echo '  pkg help                这份帮助'
    echo ''
    echo '  pkg ls 的参数透传给 pkgview：'
    echo '    pkg ls -s snap           只看 snap'
    echo '    pkg ls -s cmd -f ^git    看 PATH 里以 git 开头的命令'
    echo '  pkg apt 的参数透传给 aptui：'
    echo '    pkg apt -y               安装时自动加 -y'
    echo ''
    echo '  界面里的快捷键：pkgview 按 ?，aptui 按 Ctrl-g。'
end

# 从 fzf 的输出行里取第一列（包名）
function __pkg_names --description 'fzf 输出 → 包名列表'
    for l in $argv
        set -l f (string split ' ' (string trim -- $l))
        set -l n $f[1]
        # aptui --plain 的行以 ● / ○ 开头，包名在第二列
        if contains -- "$n" ● ○
            set n $f[2]
        end
        test -n "$n"; and echo $n
    end
end

function __pkg_fzf_se --description 'fzf 搜 apt 全部包并安装'
    set -l dir (__fishkit_dir)
    if test -z "$dir"
        return 1
    end
    set -l au $dir/bin/aptui.py

    echo '正在读取 apt 缓存（首次要扫 apt list，几秒）...' >&2
    set -l list (command python3 $au --dump '' --plain 2>/dev/null)
    if test -z "$list"
        echo 'pkg se: 读不到 apt 缓存，先跑 sudo apt update' >&2
        return 1
    end

    set -l pick (printf '%s\n' $list | fzf --multi --height=95% --layout=reverse --cycle \
        --prompt='apt > ' --pointer='▶' --marker='✓' \
        --header='[Tab] 多选 · [Enter] 安装 · [Esc] 取消 · ● 已装 ○ 未装' \
        --query=(string join ' ' $argv) \
        --preview "command python3 $au --show {2}" \
        --preview-window 'right:60%:wrap')
    if test -z "$pick"
        return 0
    end

    set -l pkgs (__pkg_names $pick)
    if test -z "$pkgs"
        return 0
    end
    echo '将安装:' $pkgs
    command sudo apt-get install $pkgs
end

function __pkg_fzf_rm --description 'fzf 搜已安装的包并卸载'
    set -l dir (__fishkit_dir)
    if test -z "$dir"
        return 1
    end
    set -l pv $dir/bin/pkgview.py
    set -l au $dir/bin/aptui.py

    set -l list (command python3 $pv --dump apt 2>/dev/null)
    if test -z "$list"
        echo 'pkg rm: 列不出已安装的包' >&2
        return 1
    end

    set -l pick (printf '%s\n' $list | fzf --multi --height=95% --layout=reverse --cycle \
        --prompt='remove > ' --pointer='▶' --marker='✓' \
        --header='[Tab] 多选 · [Enter] 卸载 · [Esc] 取消' \
        --query=(string join ' ' $argv) \
        --preview "command python3 $au --show {1}" \
        --preview-window 'right:60%:wrap')
    if test -z "$pick"
        return 0
    end

    set -l pkgs (__pkg_names $pick)
    if test -z "$pkgs"
        return 0
    end
    echo '将卸载:' $pkgs
    command sudo apt-get remove $pkgs
end

function pkg --description '已安装程序查看 / apt 包搜索安装（pkgview + aptui 统一入口）'
    if not type -q python3
        echo "pkg: 需要 python3，先跑 sudo apt-get install -y python3" >&2
        return 1
    end

    set -l dir (__fishkit_dir)
    if test -z "$dir"
        return 1
    end

    set -l pv $dir/bin/pkgview.py
    set -l au $dir/bin/aptui.py
    for f in $pv $au
        if not test -f $f
            echo "pkg: 找不到 $f" >&2
            return 1
        end
    end

    # 注意：fish 里空列表在引号中会展开成 0 个参数，直接 switch 会报错，所以先兜住
    set -l sub ''
    if set -q argv[1]
        set sub $argv[1]
    end
    set -l rest
    if set -q argv[2]
        set rest $argv[2..-1]
    end

    switch $sub
        case -h --help help usage
            __pkg_usage

        case '' ls list installed i
            command python3 $pv $rest

        case se
            if type -q fzf
                __pkg_fzf_se $rest
            else
                echo 'pkg se: 没装 fzf，改用内置 TUI（Ctrl-g 看帮助）' >&2
                command python3 $au $rest
            end

        case rm remove un uninstall
            if type -q fzf
                __pkg_fzf_rm $rest
            else
                echo 'pkg rm: 没装 fzf，改用内置 TUI（Ctrl-x 卸载）' >&2
                command python3 $pv $rest
            end

        case apt install add
            # 非选项参数拼成初始搜索词： pkg apt gcc → 打开就搜 gcc
            set -l flags (string match -- '-*' $rest)
            set -l words (string match -v -- '-*' $rest)
            if set -q words[1]
                command python3 $au $flags -q (string join ' ' $words)
            else
                command python3 $au $flags
            end

        case find grep search s
            set -l pat ''
            set -l src ''
            if set -q rest[1]
                set pat $rest[1]
            end
            if set -q rest[2]
                set src $rest[2]
            end
            if test -z "$pat"
                echo '用法: pkg find <正则> [数据源: all apt snap flatpak pip cmd]' >&2
                return 1
            end
            test -z "$src"; and set src apt
            command python3 $pv --dump $src -f "$pat"

        case which where owner
            set -l cmd ''
            if set -q rest[1]
                set cmd $rest[1]
            end
            if test -z "$cmd"
                echo '用法: pkg which <命令>' >&2
                return 1
            end
            set -l p (command -v $cmd 2>/dev/null)
            if test -z "$p"
                echo "$cmd: 不在 PATH 里" >&2
                return 1
            end
            set -l real (path resolve $p 2>/dev/null)
            test -z "$real"; and set real $p
            echo "$cmd → $real"
            set -l owner (dpkg -S $real 2>/dev/null | string replace -r ':.*$' '')
            if test -n "$owner"
                echo ' 包: '(string join ' ' (string split ',' -- $owner))
                set -l ver (dpkg-query -W -f '${Version}\n' $owner 2>/dev/null)
                test -n "$ver"; and echo ' 版本: '(string join ' ' $ver)
            else
                echo ' 没有 dpkg 包提供它（手动编译、脚本、或 pip/npm 之类）'
            end

        case '*'
            # 裸参数当正则用： pkg nginx ≈ pkg find nginx
            command python3 $pv --dump apt -f (string join ' ' $argv)
    end
end
