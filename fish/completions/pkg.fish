# fishkit 的 fish 补全：pkg 的子命令与参数
complete -c pkg -f

# 子命令
complete -c pkg -n '__fish_use_subcommand' -a ls -d '打开 TUI 浏览已安装程序'
complete -c pkg -n '__fish_use_subcommand' -a find -d '按正则列出已安装的包（非交互）'
complete -c pkg -n '__fish_use_subcommand' -a se -d 'fzf 搜索 apt 全部包并安装'
complete -c pkg -n '__fish_use_subcommand' -a rm -d 'fzf 搜索已安装的包并卸载'
complete -c pkg -n '__fish_use_subcommand' -a apt -d '内置 TUI：搜索 apt 缓存并安装'
complete -c pkg -n '__fish_use_subcommand' -a which -d '这个命令是哪个包装的'
complete -c pkg -n '__fish_use_subcommand' -a help -d '帮助'

# pkg ls / list / installed —— 透传给 pkgview
complete -c pkg -n '__fish_seen_subcommand_from ls list installed' \
    -s s -l source -x -a 'all apt snap flatpak pip cmd' -d '数据源'
complete -c pkg -n '__fish_seen_subcommand_from ls list installed' \
    -s f -l filter -x -d '初始正则'
complete -c pkg -n '__fish_seen_subcommand_from ls list installed' \
    -s n -l limit -x -d '最多输出条数'

# pkg apt / install —— 透传给 aptui
complete -c pkg -n '__fish_seen_subcommand_from apt install add search' \
    -s y -l yes -d '安装时自动加 -y'
complete -c pkg -n '__fish_seen_subcommand_from apt install add search' \
    -s q -l query -x -d '预填搜索词'

# pkg se / rm —— 关键词直接透传给 fzf 的 --query
complete -c pkg -n '__fish_seen_subcommand_from se rm remove un' -d '搜索关键词' -f

# pkg find <正则> [数据源]
complete -c pkg -n '__fish_seen_subcommand_from find grep; and test (count (commandline -opc)) -ge 2' \
    -a 'all apt snap flatpak pip cmd' -d '数据源'

# pkg which <命令>
complete -c pkg -n '__fish_seen_subcommand_from which where owner' \
    -a '(__fish_complete_command)' -d '命令'
