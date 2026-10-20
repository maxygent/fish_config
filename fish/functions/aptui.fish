function aptui --description 'fzf 风格的 apt 包搜索 / 安装 TUI'
    set -l dir (__fishkit_dir)
    if test -z "$dir"
        return 1
    end
    set -l s $dir/bin/aptui.py
    if not test -f $s
        echo "aptui: 找不到 $s" >&2
        return 1
    end
    command python3 $s $argv
end
