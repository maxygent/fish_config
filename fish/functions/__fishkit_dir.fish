function __fishkit_dir --description '返回 fishkit 的安装目录（找不到就报错）'
    if set -q FISHKIT_DIR; and test -d "$FISHKIT_DIR/bin"
        echo $FISHKIT_DIR
        return 0
    end
    echo 'fishkit: 没找到安装目录，重跑一次 install.sh 即可修复' >&2
    # 注意：fish 没有 bash 的 ${VAR:-default} 写法，这里分开判断
    if set -q FISHKIT_DIR
        echo "  bash $FISHKIT_DIR/install.sh" >&2
    else
        echo '  bash ~/fishkit/install.sh' >&2
    end
    return 1
end
