# ---------------------------------------------------------------------------
# montys — fish 主题
# 移植自 oh-my-posh 的 montys.omp.json（Windows 端 ~/.poshthemes/montys.omp.json）
# 依赖：终端字体需支持 Nerd Font（WezTerm / Windows Terminal 等）
# ---------------------------------------------------------------------------

function fish_prompt
    set -l last_status $status

    # ── 配色（与 montys.omp.json 一一对应，可自行微调）─────────────────
    set -l os_bg    003543   # OS 段背景
    set -l os_fg    00c983   # OS 段前景
    set -l path_bg  DA627D   # 路径段
    set -l git_bg   FCA17D   # git 段
    set -l node_bg  76b367   # node 段
    set -l exec_bg  83769c   # 耗时段
    set -l time_bg  33658A   # 时间段
    set -l ok_bg    2e9599   # 状态段：成功
    set -l err_bg   E06C75   # 状态段：失败
    set -l seg_fg   ffffff
    set -l p_user   CD4277   # 第二行：用户名
    set -l p_bolt   45F1C2   # 第二行：⚡ 与第二个 ❯
    set -l p_arrow  26C6DA   # 第二行：第一个 ❯
    set -l p_root   cd5e42   # 第二行：root 图标

    # ── 图标 / 分隔符 ─────────────────────────────────────────────────
    set -l sep_l   ""          # 左菱形 U+E0B6
    set -l sep     ""            # 箭头   U+E0B0
    set -l sep_r   ""          # 右菱形 U+E0B4
    set -l i_os    ""           # Linux  U+E712
    set -l i_fold  " "     # 目录 U+F07B U+EA9C
    set -l i_br    ""       # 分支   U+E0A0
    set -l i_stash ""        # stash  U+EB4B
    set -l i_node  ""         # node   U+E718
    set -l i_exec  ""         # 计时   U+EBA2
    set -l i_time  ""         # 时钟   U+F017
    set -l i_ok    ""           # 成功   U+F469
    set -l i_err   ""          # 失败   U+F421
    set -l i_root  ""         # root   U+E3BF

    # ── 先探测各段是否显示，才能决定箭头指向哪一段 ─────────────────────
    set -l show_git  0
    set -l show_node 0
    set -l git_head  ""
    set -l git_stash 0
    set -l node_ver  ""

    if command git rev-parse --is-inside-work-tree >/dev/null 2>&1
        set show_git 1
        set git_head (command git symbolic-ref --quiet --short HEAD 2>/dev/null)
        test -n "$git_head"
        or set git_head (command git rev-parse --short HEAD 2>/dev/null)
        set git_stash (count (command git stash list 2>/dev/null))
    end

    if test -f package.json; or test -f package-lock.json
        # node --version 较慢，结果缓存在全局变量，只查一次
        set -q __montys_node_ver
        or set -g __montys_node_ver (node --version 2>/dev/null | string replace -r '^v' '')
        if test -n "$__montys_node_ver"
            set show_node 1
            set node_ver $__montys_node_ver
        end
    end

    set -l after_path $exec_bg
    test $show_node -eq 1; and set after_path $node_bg
    test $show_git  -eq 1; and set after_path $git_bg

    set -l after_git $exec_bg
    test $show_node -eq 1; and set after_git $node_bg

    set -l status_bg $ok_bg
    test $last_status -ne 0; and set status_bg $err_bg

    # ══ 第一行 ═══════════════════════════════════════════════════════

    # 段 1：OS / 主机名
    set_color -b normal $os_bg
    echo -n $sep_l
    set_color -b $os_bg $os_fg
    echo -n " $i_os "(prompt_hostname)" "

    # 段 2：路径（想显示完整路径，把 prompt_pwd 换成 $PWD）
    set_color -b $path_bg $os_bg
    echo -n $sep
    set_color -b $path_bg 000000
    echo -n " $i_fold "
    set_color -b $path_bg $seg_fg
    echo -n " "(prompt_pwd)" "

    # 段 3：git
    if test $show_git -eq 1
        set_color -b $git_bg $path_bg
        echo -n $sep
        set_color -b $git_bg $seg_fg
        echo -n " ➜ ("
        echo -n " $i_br "$git_head
        test $git_stash -gt 0
        and echo -n " $i_stash "$git_stash
        echo -n " ) "
        set_color -b $after_git $git_bg
        echo -n $sep
    else
        set_color -b $after_path $path_bg
        echo -n $sep
    end

    # 段 4：node
    if test $show_node -eq 1
        set_color -b $node_bg $seg_fg
        echo -n " $i_node "$node_ver" "
        set_color -b $exec_bg $node_bg
        echo -n $sep
    end

    # 段 5：上条命令耗时
    set -l dur $CMD_DURATION
    test -n "$dur"; or set dur 0
    set -l dur_str
    if test $dur -lt 1000
        set dur_str $dur"ms"
    else if test $dur -lt 60000
        set dur_str (math -s1 "$dur / 1000")"s"
    else
        set dur_str (math -s0 "$dur / 60000")"m"(math -s0 "$dur % 60000 / 1000")"s"
    end
    set_color -b $exec_bg $seg_fg
    echo -n " $i_exec "$dur_str" "
    set_color -b $time_bg $exec_bg
    echo -n $sep

    # 段 6：时间
    set_color -b $time_bg $seg_fg
    echo -n " $i_time "(date +"%-I:%M %p")" "
    set_color -b $status_bg $time_bg
    echo -n $sep

    # 段 7：上条命令的退出状态
    set_color -b $status_bg $seg_fg
    if test $last_status -ne 0
        echo -n " $i_err "
    else
        echo -n " $i_ok "
    end
    set_color -b normal $status_bg
    echo -n $sep_r
    set_color normal

    # ══ 第二行 ═══════════════════════════════════════════════════════
    echo

    if test "$USER" = root
        set_color $p_root
        echo -n " $i_root "
    end

    set_color -o $p_bolt
    echo -n "⚡"
    set_color -o $p_user
    echo -n "$USER"
    set_color normal
    echo -n " "
    set_color $p_arrow
    echo -n "❯"
    set_color $p_bolt
    echo -n "❯"
    set_color normal
    echo -n " "
end
