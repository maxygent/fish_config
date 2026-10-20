function fish_mode_prompt --description '只在非插入模式显示 [N] / [V] / [R]'
    # 你用的是 vi 键位，fish 默认会在最左边一直挂着 [I]。
    # 参考仓库的提示符是干净的，所以插入模式（日常状态）不显示，切到普通/可视/替换模式才提示。
    # 想恢复原样：删掉这个文件即可。
    test "$fish_bind_mode" = insert; and return
    fish_default_mode_prompt
end
