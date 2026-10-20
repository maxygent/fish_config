# =============================================================================
#  proxy.fish — WSL (fish) 终端代理开关
#
#  安装：cp proxy.fish ~/.config/fish/conf.d/proxy.fish
#  用法：proxy-on [socks5|http] [-f]   开启（默认直奔上次成功的端点，-f 强制重探）
#        proxy-off [--forget]          关闭（--forget 同时忘掉缓存端点）
#        proxy-status                  查看状态 + 测试 Google / GitHub
#
#  提示符：代理开启时提示符右侧出现「 PROXY 」色块，关闭即消失。
#          不喜欢可 set -Ux PROXY_NO_PROMPT 1 关掉。
#
#  默认：只认一个端口 —— socks5 宿主:10808（v2rayN 的 socks 端口）。
#        默认不去猜 10809：只有一个端口时多试一个，只是白白多等几秒。
#  覆盖：set -Ux PROXY_PORT 10808        换端口
#        set -Ux PROXY_HTTP_PORT 10809   确实有独立 http 端口时再设它
#        set -Ux PROXY_HOST 172.x.x.x    固定 Windows 宿主 IP
#
#  提速：首次探测成功后把端点记进 universal 变量 PROXY_CACHED；
#        之后 proxy-on 只做一次 TCP 探测（毫秒级），不再跑 curl 试探。
#        连这一次都想省：set -Ux PROXY_NO_CHECK 1（直接信任缓存）
#
#  说明：WSL2 默认 NAT 模式，127.0.0.1 指 WSL 自己，必须走宿主 IP（默认网关）；
#        若 .wslconfig 开了 networkingMode=mirrored，则 127.0.0.1 也可用。
# =============================================================================

set -q PROXY_PORT;          or set -g PROXY_PORT 10808
set -q PROXY_HTTP_PORT;     or set -g PROXY_HTTP_PORT ''
set -q PROXY_HOST;          or set -g PROXY_HOST ''
set -q PROXY_TIMEOUT;       or set -g PROXY_TIMEOUT 8
set -q PROXY_PROBE_TIMEOUT; or set -g PROXY_PROBE_TIMEOUT 4

# ------------------------------------------------------------------ 内部工具 --
function __proxy_grey -a msg
    set_color brblack
    echo $msg
    set_color normal
end

# 宿主 IP 候选：默认网关优先，其次 resolv.conf 里非本机/非 WSL-DNS 的地址
function __proxy_win_ips
    for ip in (ip route show default 2>/dev/null | string match -rg 'via (\S+)')
        echo $ip
    end
    if test -r /etc/resolv.conf
        for ip in (string match -rg '^\s*nameserver\s+(\S+)' < /etc/resolv.conf)
            test "$ip" = 127.0.0.53; and continue
            test "$ip" = 10.255.255.254; and continue
            echo $ip
        end
    end
end

# "socks5h://172.20.0.1:10808" → 输出 scheme host port 三个元素
function __proxy_parse -a url
    string match -rg '^([a-z0-9]+)://([^:/]+):(\d+)$' -- $url
end

# socks5 / socks5h 算一家，用来判断缓存端点能不能顶替用户要的协议
function __proxy_family -a scheme
    switch "$scheme"
        case socks5 socks5h socks
            echo socks
        case http https
            echo http
        case '*'
            echo other
    end
end

# 缓存的端点还能不能用：默认只做一次 TCP 探测，通了就直接用
function __proxy_cached_ok
    set -q PROXY_CACHED; and test -n "$PROXY_CACHED"; or return 1
    set -l p (__proxy_parse $PROXY_CACHED)
    test (count $p) -eq 3; or return 1
    set -q PROXY_NO_CHECK; and return 0
    __proxy_tcp_check $p[2] $p[3]
end

# 端口探测：有硬超时，且结果做缓存（避免同一端口反复等）
function __proxy_tcp_check -a host port
    set -l key "$host:$port"
    set -q __proxy_dead; or set -g __proxy_dead
    contains -- $key $__proxy_dead; and return 1

    set -l to
    command -q timeout; and set to timeout 1

    if command -q nc
        $to nc -z $host $port >/dev/null 2>&1
        set -l st $status
        test $st -eq 0; and return 0
        test $st -eq 124; and begin
            set -a -g __proxy_dead $key
            return 1
        end
    end
    if command -q python3
        $to python3 -c 'import socket,sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), 1).close()
except Exception:
    sys.exit(1)' $host $port >/dev/null 2>&1
        set -l st $status
        test $st -eq 0; and return 0
        test $st -eq 124; and begin
            set -a -g __proxy_dead $key
            return 1
        end
    end
    if command -q bash
        $to bash -c "exec 3<>/dev/tcp/$host/$port" >/dev/null 2>&1; and return 0
    end

    set -a -g __proxy_dead $key
    return 1
end

# 返回 "<http_code> <time_total>"；proxy 为空则强制直连；第三个参数可临时指定超时
function __proxy_probe -a proxy url tmo
    set -l wait $PROXY_TIMEOUT
    test -n "$tmo"; and set wait $tmo
    set -l out ''
    if test -n "$proxy"
        set out (curl -s -o /dev/null -w '%{http_code} %{time_total}' \
            --max-time $wait --proxy $proxy $url 2>/dev/null)
    else
        set out (curl -s -o /dev/null -w '%{http_code} %{time_total}' \
            --max-time $wait --noproxy '*' $url 2>/dev/null)
    end
    if test -z "$out"
        echo '000 0.00'
        return 1
    end
    echo $out
end

function __proxy_code -a res
    set -l arr (string split ' ' -- $res)
    if test (count $arr) -gt 0
        echo $arr[1]
    else
        echo 000
    end
end

function __proxy_row -a label url proxy
    set -l res (__proxy_probe "$proxy" $url)
    set -l arr (string split ' ' -- $res)
    set -l code 000
    test (count $arr) -gt 0; and set code $arr[1]
    set -l t 0
    test (count $arr) -gt 1; and set t (math "round($arr[2]*100)/100")

    set -l mark (set_color red)"x"(set_color normal)
    set -l note '超时 / 不可达'
    if test "$code" = 204 -o "$code" = 200
        set mark (set_color green)"v"(set_color normal)
        set note "$t s"
    else if test "$code" != 000
        set mark (set_color yellow)"!"(set_color normal)
        set note "HTTP $code  ($t s)"
    end
    printf '  %s %-7s %-44s %s\n' "$mark" $label $url $note
end

# ------------------------------------------------------------------- proxy-on --
function proxy-on -d '开启 WSL 终端代理'
    set -l wants socks5 http
    set -l force 0

    # 先吃掉选项，剩下的当协议名
    set -l args
    for a in $argv
        switch "$a"
            case -f --fresh --probe
                set force 1
            case '*'
                set -a args $a
        end
    end
    set -l proto ''
    if set -q args[1]
        set proto $args[1]
    end

    switch "$proto"
        case http
            set wants http
        case socks5 socks socks5h
            set wants socks5
        case ''
            # 自动
        case '*'
            echo '用法: proxy-on [socks5|http] [-f]'
            return 1
    end

    set -g __proxy_dead

    # 0) 端点缓存：上次通了就直接用，只做一次毫秒级 TCP 探测，跳过全部 curl 试探
    set -l chosen ''
    set -l verified 0
    if test $force -eq 0; and __proxy_cached_ok
        set -l cp (__proxy_parse $PROXY_CACHED)
        if test -z "$proto"; or test (__proxy_family $cp[1]) = (__proxy_family $proto)
            set chosen $PROXY_CACHED
            __proxy_grey "  缓存命中：$chosen（要重新探测加 -f）"
        end
    end

    if test -z "$chosen"
        set -l hosts 127.0.0.1
        if test -n "$PROXY_HOST"
            set hosts $PROXY_HOST
        else
            for h in (__proxy_win_ips)
                contains -- $h $hosts; or set -a hosts $h
            end
        end

        if test -n "$PROXY_HTTP_PORT"
            __proxy_grey "  探测中：端口 $PROXY_PORT / $PROXY_HTTP_PORT"
        else
            __proxy_grey "  探测中：端口 $PROXY_PORT（没配 PROXY_HTTP_PORT，只试这一个）"
        end

        # 1) 找出"端口在监听"的候选；命中一个宿主就停止，避免挨个试
        set -l cands
        for h in $hosts
            set -l local_cands
            if contains -- socks5 $wants; and __proxy_tcp_check $h $PROXY_PORT
                set -a local_cands "socks5h:$h:$PROXY_PORT"
            end
            if contains -- http $wants
                # 没配独立 http 端口时，只顺带试同一个端口（mixed 端口才这么用）
                set -l ports $PROXY_HTTP_PORT
                test -n "$ports"; or set ports $PROXY_PORT
                for p in $ports $PROXY_PORT
                    contains -- "http:$h:$p" $local_cands; and continue
                    __proxy_tcp_check $h $p; and set -a local_cands "http:$h:$p"
                end
            end
            if test (count $local_cands) -gt 0
                set cands $local_cands
                break
            end
        end

        if test (count $cands) -eq 0
            set_color red
            echo "x 没有可达的代理端口（$PROXY_PORT）"
            set_color normal
            set -e -U PROXY_CACHED
            __proxy_grey '  1. Windows 代理软件需勾选「允许来自局域网的连接」'
            __proxy_grey '  2. 检查 Windows 防火墙是否放行该端口'
            __proxy_grey '  3. 也可固定宿主：set -Ux PROXY_HOST <Windows 主机 IP>'
            return 1
        end

        # 2) 用真实请求挑出能通的那一个（超时用 PROXY_PROBE_TIMEOUT，别死等 8 秒）
        set -l best ''
        set -l verified 0
        for c in $cands
            set -l cp (string split : $c)
            set -l r (__proxy_probe "$cp[1]://$cp[2]:$cp[3]" \
                https://www.gstatic.com/generate_204 $PROXY_PROBE_TIMEOUT)
            set -l code (__proxy_code $r)
            if test "$code" = 204 -o "$code" = 200
                set best $c
                set verified 1
                break
            end
        end
        test -n "$best"; or set best $cands[1]
        set chosen $best
    end

    set -l p (string split : $chosen)
    set -l scheme $p[1]
    set -l host $p[2]
    set -l port $p[3]

    # 3) 写入会话环境变量
    proxy-off >/dev/null 2>&1

    set -l http_url ''
    if test "$scheme" = socks5h
        set -gx all_proxy "socks5h://$host:$port"
        if test -n "$PROXY_HTTP_PORT"; and __proxy_tcp_check $host $PROXY_HTTP_PORT
            set http_url "http://$host:$PROXY_HTTP_PORT"
        else
            set http_url "socks5h://$host:$port"
        end
        set -gx http_proxy $http_url
    else
        set http_url "http://$host:$port"
        set -gx http_proxy $http_url
        set -gx all_proxy $http_url
    end
    set -gx https_proxy $http_proxy
    set -gx ftp_proxy $http_proxy
    set -gx rsync_proxy $http_proxy
    set -gx no_proxy 'localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,*.local,*.internal'
    set -gx HTTP_PROXY $http_proxy
    set -gx HTTPS_PROXY $https_proxy
    set -gx ALL_PROXY $all_proxy
    set -gx NO_PROXY $no_proxy
    set -g PROXY_ACTIVE "$scheme://$host:$port"

    # 4) 记住这次真正验证过的端点；没验证过（探测全失败）就不写，免得缓存一个坏端点
    if test $verified -eq 1
        set -l want_ep "$scheme://$host:$port"
        set -l cur_ep ''
        set -q PROXY_CACHED; and set cur_ep $PROXY_CACHED
        if test "$cur_ep" != "$want_ep"
            set -U PROXY_CACHED $want_ep
        end
    end

    set_color green
    echo "v 已开启：$scheme://$host:$port"
    set_color normal
    printf '   %-12s %s\n' http_proxy $http_proxy
    printf '   %-12s %s\n' https_proxy $https_proxy
    printf '   %-12s %s\n' all_proxy $all_proxy
    __proxy_grey '   （仅当前 fish 会话有效，提示符右侧会出现 PROXY 标记，proxy-status 验证）'
end

# ------------------------------------------------------------------ proxy-off --
function proxy-off -d '关闭终端代理（--forget 同时忘掉缓存端点）'
    set -l forget 0
    contains -- --forget $argv; and set forget 1
    contains -- -r $argv; and set forget 1
    contains -- --reset $argv; and set forget 1

    set -l had 0
    for v in http_proxy https_proxy all_proxy ftp_proxy rsync_proxy no_proxy \
             HTTP_PROXY HTTPS_PROXY ALL_PROXY FTP_PROXY NO_PROXY PROXY_ACTIVE
        if set -q $v
            set -e -g $v
            set had 1
        end
    end
    if test $had -eq 1
        set_color green
        echo 'v 代理已关闭'
        set_color normal
    else
        __proxy_grey '代理本来就是关的'
    end

    if test $forget -eq 1
        set -e -U PROXY_CACHED
        __proxy_grey '已忘掉缓存的端点，下次 proxy-on 重新探测'
    end
end

# --------------------------------------------------------------- proxy-status --
function proxy-status -d '查看代理状态并测试 Google / GitHub'
    set -g __proxy_dead

    set -l proxy
    if set -q http_proxy; and test -n "$http_proxy"
        set proxy $http_proxy
    else if set -q all_proxy; and test -n "$all_proxy"
        set proxy $all_proxy
    end

    set_color brblue
    echo '── 代理状态 ──────────────────────────────────────────────'
    set_color normal
    if test -n "$proxy"
        printf '  生效代理   %s\n' $proxy
        if set -q all_proxy; and test -n "$all_proxy"
            printf '  all_proxy  %s\n' $all_proxy
        end
        if set -q PROXY_ACTIVE
            printf '  探测命中   %s\n' $PROXY_ACTIVE
        end
        if set -q PROXY_CACHED; and test -n "$PROXY_CACHED"
            printf '  端点缓存   %s\n' $PROXY_CACHED
        end
        if set -q no_proxy; and test -n "$no_proxy"
            printf '  直连白名单 %s\n' (string sub -l 44 -- $no_proxy)'...'
        end
    else
        set_color yellow
        echo '  当前会话未开启代理'
        set_color normal
        __proxy_grey '  运行 proxy-on 开启；下面先做一次直连测试'
    end

    set_color brblue
    echo '── 连通性 ────────────────────────────────────────────────'
    set_color normal
    set -l r1 (__proxy_probe "$proxy" https://www.gstatic.com/generate_204)
    set -l r2 (__proxy_probe "$proxy" https://api.github.com/zen)
    __proxy_row Google https://www.gstatic.com/generate_204 "$proxy"
    __proxy_row GitHub https://api.github.com/zen "$proxy"

    set -l ok 0
    for r in $r1 $r2
        set -l c (__proxy_code $r)
        if test "$c" = 204 -o "$c" = 200
            set ok (math $ok + 1)
        end
    end

    if test -n "$proxy"
        set_color brblue
        echo '── 直连对照（不走代理）────────────────────────────────────'
        set_color normal
        __proxy_row Google https://www.gstatic.com/generate_204 ''
        __proxy_row GitHub https://api.github.com/zen ''
    end

    set -l via '代理'
    test -n "$proxy"; or set via '直连'

    echo
    if test $ok -eq 2
        set_color green
        echo "  结论：$via下 Google + GitHub 均连通"
        set_color normal
        return 0
    else if test $ok -eq 1
        set_color yellow
        echo "  结论：$via下只有 1 项连通，检查代理规则 / 分流设置"
        set_color normal
        return 1
    else
        set_color red
        if test -z "$proxy"
            echo '  结论：直连两项都不通，需要 proxy-on 走代理'
        else
            echo '  结论：两项均不通，代理未生效或上游节点不可用'
        end
        set_color normal
        return 1
    end
end

# ----------------------------------------------------- 提示符代理指示器 ------
# 代理是否处于开启状态（http_proxy / all_proxy 任一非空即视为开启）
function __proxy_is_on
    if set -q http_proxy; and test -n "$http_proxy"
        return 0
    end
    if set -q all_proxy; and test -n "$all_proxy"
        return 0
    end
    return 1
end

# 覆盖主题自带的 fish_right_prompt：只留 PROXY 色块，不再重复显示时钟
# （左侧提示符已经有时间了；conf.d 里 omf.fish 先加载、proxy.fish 后加载，所以这里定义会生效）
function fish_right_prompt -d '右侧提示符：只在代理开启时显示 PROXY 指示灯'
    if not set -q PROXY_NO_PROMPT; and __proxy_is_on
        set_color -b 2e9599 ffffff
        echo -n '  PROXY  '
        set_color normal
    end
end

# -------------------------------------------------------- 下划线写法别名 ------
# 习惯敲 proxy_on 的话也不用改，包一层就行
function proxy_on; proxy-on $argv; end
function proxy_off; proxy-off $argv; end
function proxy_status; proxy-status $argv; end
