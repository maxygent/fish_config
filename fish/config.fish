# >>> fishkit >>>
# 这一段由 fishkit/install.sh 维护：重跑安装会整段替换，别在这里手改。
# 要调整就改 fishkit/fish/config.fish，然后重新执行 install.sh。
set -g fish_term24bit 1

# 中文环境。若系统没装 zh_CN.UTF-8，换成 C.UTF-8 或注释掉这行。
set LANG zh_CN.UTF8

if status is-interactive
    # 不要开屏欢迎语（参考仓库也这么设）
    set fish_greeting

    # 提示符：具体用哪套由 install.sh 写进 conf.d/fishkit.fish 的 FISHKIT_PROMPT
    if test "$FISHKIT_PROMPT" = starship; and type -q starship
        starship init fish | source
    end

    # 目录跳转（没装就静默跳过）
    type -q zoxide; and zoxide init fish | source
end
# <<< fishkit <<<
