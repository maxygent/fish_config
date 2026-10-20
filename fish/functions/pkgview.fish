function pkgview --description 'TUI 查看已安装的程序，支持正则搜索'
    set -l dir (__fishkit_dir)
    if test -z "$dir"
        return 1
    end
    set -l s $dir/bin/pkgview.py
    if not test -f $s
        echo "pkgview: 找不到 $s" >&2
        return 1
    end
    command python3 $s $argv
end
