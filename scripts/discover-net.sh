#!/bin/sh
# Fleet network discovery. Read-only facts for the namespace topology:
# addresses, routes, listening ports, Docker publish ports, tunnels, DNAT rules.
#
#   ssh <target> sh -s < <server-discovery>/scripts/discover-net.sh
#
# Same section format as discover.sh. Uses sudo -n only where it works without a
# password (process names in ss, wg show, iptables); nothing here changes the host.
export LC_ALL=C LANG=C
section() { printf '### %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
SUDO=""
if [ "$(id -u 2>/dev/null)" != 0 ] && have sudo && sudo -n true 2>/dev/null; then SUDO="sudo -n"; fi

section hostname; hostname 2>/dev/null
section addr; ip -brief addr 2>/dev/null || ifconfig 2>/dev/null
section route; ip route 2>/dev/null || route -n 2>/dev/null
section links; ls /sys/class/net 2>/dev/null | tr '\n' ' '; echo
section listen; $SUDO ss -tulpn 2>/dev/null || $SUDO netstat -tulpn 2>/dev/null
section docker_ps; have docker && docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null
section compose; have docker && docker compose ls --all --format json 2>/dev/null
section wg; have wg && $SUDO wg show 2>/dev/null
section openvpn; pgrep -a openvpn 2>/dev/null
section resolv; grep -E '^(nameserver|search)' /etc/resolv.conf 2>/dev/null
section hosts; grep -vE '^\s*(#|$)' /etc/hosts 2>/dev/null
section ssh_config; grep -iE '^\s*(Host|HostName|ProxyJump|Port|User)\b' ~/.ssh/config 2>/dev/null
section nat; $SUDO iptables -t nat -S PREROUTING 2>/dev/null | grep -i dnat; $SUDO nft list ruleset 2>/dev/null | grep -iE 'dnat|redirect' | head -40
section uci_firewall; have uci && uci show firewall 2>/dev/null | grep -E '=redirect|redirect\[[0-9]+\]\.(name|src_dport|dest_ip|dest_port|src_ip|proto)|=zone|zone\[[0-9]+\]\.(name|network)|=forwarding|forwarding\[[0-9]+\]\.(src|dest)'
section uci_network; have uci && uci show network 2>/dev/null | grep -E '\.(proto|ipaddr|netmask|device|private_key|public_key|endpoint_host|endpoint_port|allowed_ips|listen_port|addresses)=' | sed -E 's/(private_key=).*/\1<redacted>/'
section uci_dhcp; have uci && uci show dhcp 2>/dev/null | grep -E 'host\[[0-9]+\]\.(name|ip|mac)='
section end
