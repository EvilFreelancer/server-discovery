#!/bin/sh
# Fleet discovery. Read-only facts for the host frontmatter card.
#
#   ssh <target> sh -s < <server-discovery>/scripts/discover.sh   (what discover.py does)
#   sh discover.sh                                                (on the machine itself)
#
# Prints sections "### <name>" followed by raw tool output; discover.py next to this file maps
# them onto the frontmatter keys. Every probe is optional so the script also runs on
# busybox (OpenWrt) and Synology DSM. Nothing here changes the host.

# Neutral locale: lsblk and friends otherwise format numbers with the client's decimal comma.
export LC_ALL=C LANG=C

section() { printf '### %s\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

section hostname; hostname 2>/dev/null
section fqdn; hostname -f 2>/dev/null
section user; id -un 2>/dev/null
section uid; id -u 2>/dev/null
section groups; id -Gn 2>/dev/null
section os_release; cat /etc/os-release 2>/dev/null
section openwrt_release; cat /etc/openwrt_release 2>/dev/null
section synoinfo; grep -hE '^(productversion|buildnumber)=' /etc.defaults/VERSION /etc/VERSION 2>/dev/null
section kernel; uname -r 2>/dev/null
section arch; uname -m 2>/dev/null
section lscpu; have lscpu && lscpu 2>/dev/null
section cpuinfo
grep -m1 -E '^(model name|cpu model|Processor)' /proc/cpuinfo 2>/dev/null
printf 'nproc=%s\n' "$(nproc 2>/dev/null || grep -c '^processor' /proc/cpuinfo 2>/dev/null)"
section meminfo; grep -E '^MemTotal' /proc/meminfo 2>/dev/null
section lsblk; have lsblk && lsblk -P -o NAME,SIZE,TYPE,ROTA,MOUNTPOINT,PKNAME 2>/dev/null
section sudo
if [ "$(id -u 2>/dev/null)" = 0 ]; then
  echo root
elif have sudo && sudo -n true 2>/dev/null; then
  echo nopasswd
elif have sudo && id -Gn 2>/dev/null | tr ' ' '\n' | grep -qxE 'sudo|wheel|admin|administrators'; then
  echo password
else
  echo none
fi
section sshd
SSHD=$(command -v sshd 2>/dev/null || echo /usr/sbin/sshd)
if [ "$(id -u 2>/dev/null)" = 0 ]; then
  "$SSHD" -T 2>/dev/null | grep -iE '^(passwordauthentication|permitrootlogin) '
elif have sudo && sudo -n true 2>/dev/null; then
  sudo -n "$SSHD" -T 2>/dev/null | grep -iE '^(passwordauthentication|permitrootlogin) '
else
  cat /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | grep -iE '^[[:space:]]*(passwordauthentication|permitrootlogin)[[:space:]]'
fi
section docker; have docker && docker version --format '{{.Server.Version}}' 2>/dev/null
section docker_runtimes; have docker && docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null
section nvidia
if have nvidia-smi; then
  nvidia-smi --query-gpu=index,name,memory.total,vbios_version,compute_cap,power.limit,power.max_limit,driver_version --format=csv,noheader 2>/dev/null \
  || nvidia-smi --query-gpu=index,name,memory.total,vbios_version,power.limit,power.max_limit,driver_version --format=csv,noheader 2>/dev/null
fi
section intel
# Discrete Intel GPUs (Arc, Battlemage): an Intel DRM card that has a hwmon (integrated
# graphics has none). One line per card in PCI order. VRAM, IP version and GuC come from
# debugfs, which needs root; without it those fields stay empty.
rootcat() {
  if [ "$(id -u 2>/dev/null)" = 0 ]; then cat "$1" 2>/dev/null
  elif have sudo && sudo -n true 2>/dev/null; then sudo -n cat "$1" 2>/dev/null
  fi
}
for card in /sys/class/drm/card[0-9] /sys/class/drm/card[0-9][0-9]; do
  [ -e "$card/device/vendor" ] || continue
  [ "$(cat "$card/device/vendor" 2>/dev/null)" = 0x8086 ] || continue
  hw=$(ls -d "$card"/device/hwmon/hwmon* 2>/dev/null | head -n 1)
  [ -n "$hw" ] || continue
  slot=$(basename "$(readlink -f "$card/device")")
  drv=$(basename "$(readlink -f "$card/device/driver")")
  name=$(have lspci && lspci -mm -s "$slot" 2>/dev/null | awk -F'"' '{print $6}')
  dbg=/sys/kernel/debug/dri/$slot
  gfx=$(rootcat "$dbg/i915_capabilities" | sed -n 's/^graphics version: //p' | head -n 1)
  [ -n "$gfx" ] || gfx=$(rootcat "$dbg/info" | sed -n 's/^graphics_verx100:* *\([0-9]*\)$/\1/p' | head -n 1)
  vram=$( { rootcat "$dbg/i915_gem_objects"; rootcat "$dbg/vram0_mm"; } | sed -n 's/.*total: *\([0-9]*MiB\).*/\1/p' | head -n 1)
  guc=$(rootcat "$dbg/gt0/uc/guc_info" | sed -n 's/^[[:space:]]*version: found //p' | head -n 1)
  printf 'slot=%s|driver=%s|name=%s|vram=%s|gfx=%s|guc=%s|power_max_uw=%s|power_rated_uw=%s\n' \
    "$slot" "$drv" "$name" "$vram" "$gfx" "$guc" \
    "$(cat "$hw/power1_max" 2>/dev/null)" "$(cat "$hw/power1_rated_max" 2>/dev/null)"
done
# NVIDIA display controllers straight from sysfs, so a box without pciutils or without the
# driver still shows its GPUs: address, vendor, device, class.
section pci_gpu
for d in /sys/bus/pci/devices/*; do
  [ -r "$d/vendor" ] && [ -r "$d/class" ] || continue
  case "$(cat "$d/vendor"):$(cat "$d/class")" in
    0x10de:0x03*) printf '%s %s %s %s\n' "${d##*/}" "$(cat "$d/vendor")" "$(cat "$d/device" 2>/dev/null)" "$(cat "$d/class")" ;;
  esac
done
section end
