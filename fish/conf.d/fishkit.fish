# fishkit —— 环境变量与缩写
#
# 这个文件由 install.sh 生成：两个 @…@ 占位符会换成本机的真实值。
# 不要直接改 ~/.config/fish/conf.d/fishkit.fish：重跑 install.sh 会被覆盖。
# 要改内容请改仓库里的 fish/conf.d/fishkit.fish，然后重新安装。
#
# 想换安装位置：把整个 fishkit 目录挪走，重跑一次 install.sh 即可。

# ~/.local/bin 放 starship、uv、nvim 之类，放最前面
if test -d $HOME/.local/bin
    fish_add_path -g -m $HOME/.local/bin
    # 从 Windows 侧直接拷进来的二进制常常没有执行位（9p 不带 mode），顺手补一下
    if test -f $HOME/.local/bin/starship; and not test -x $HOME/.local/bin/starship
        chmod +x $HOME/.local/bin/starship 2>/dev/null
    end
end

if not set -q FISHKIT_DIR
    set -g FISHKIT_DIR '@FISHKIT_DIR@'
end

# 提示符由谁接管：starship / montys / 留空（不接管）
if not set -q FISHKIT_PROMPT
    set -g FISHKIT_PROMPT '@FISHKIT_PROMPT@'
end

if status is-interactive
    abbr --add pk pkg
    abbr --add pka 'pkg apt'
end
