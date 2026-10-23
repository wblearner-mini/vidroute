#!/bin/sh
# Install vidroute on Linux or macOS and set it up: uv -> vidroute -> `vidroute setup` (one plan, one question).
#
#   sh install.sh [--source SPEC] [--host ID]... [--mirror auto|cn] [--yes] [--no-setup]
#   curl -LsSf https://raw.githubusercontent.com/wblearner-mini/vidroute/main/install.sh | sh
#
# 1. Finds uv, or puts uv 0.13.4 into ~/.vidroute/runtimes/uv (SHA-256 pinned below; from GitHub, else PyPI,
#    else the Aliyun PyPI mirror). Nothing is installed system-wide.
# 2. Installs vidroute with `uv tool install` (uv brings its own Python if needed); ~/.local/bin goes on PATH.
# 3. Runs `vidroute setup`: ONE plan - ffmpeg check, local speech/OCR models, the vidroute skill for every
#    agent host it finds, a self-test - and it asks before doing anything.
# Environment instead of flags (for `curl | sh`): VIDROUTE_SOURCE, VIDROUTE_MIRROR=cn, VIDROUTE_YES=1.
set -eu

UV_VERSION=0.13.4  # same as vidroute's own bootstrap; hashes: GitHub release digests and PyPI (2026-10-23)
PYPI_MIRROR=https://mirrors.aliyun.com/pypi/simple/
PYTHON_MIRROR=https://registry.npmmirror.com/-/binary/python-build-standalone/
source_spec=${VIDROUTE_SOURCE:-git+https://github.com/wblearner-mini/vidroute}
mirror=${VIDROUTE_MIRROR:-auto}
yes=${VIDROUTE_YES:-0}
setup=1
hosts=""
vhome=${VIDROUTE_HOME:-$HOME/.vidroute}

say() { printf 'vidroute-install: %s\n' "$*" >&2; }
die() { say "$*"; exit 1; }

while [ $# -gt 0 ]; do
  case $1 in
    --source) source_spec=$2; shift 2 ;;
    --host) hosts="$hosts $2"; shift 2 ;;
    --mirror) mirror=$2; shift 2 ;;
    --yes|-y) yes=1; shift ;;
    --no-setup) setup=0; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)
    tarball=uv-x86_64-unknown-linux-gnu.tar.gz; tar_sha=2b428396f12572d4429cf210ca234dd599a33b3af0958a4d5ed569d63e3f344f
    wheel=packages/4b/a1/de02c0913726728eca56ee258875cbba9c7b0572c63f494fd9f55c7852e3/uv-0.13.4-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
    wheel_sha=05e4332791b4d55b6dde8695b7fa6079b26991519f44760887c5dfbd5ca4fe53 ;;
  Linux-aarch64|Linux-arm64)
    tarball=uv-aarch64-unknown-linux-gnu.tar.gz; tar_sha=1ae72b4718729e2ccc4f1ae2c6d1e531ca8467800e0546e9130b3886023a60ab
    wheel=packages/f8/39/be14f051bf34a1822d634819890a2d0cba3b69101e633fb7b9f54d7b4020/uv-0.13.4-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64.whl
    wheel_sha=e90ab42825587821096cb0d071e387468da38cfa22818c91f9c0b3c0453048e8 ;;
  Darwin-x86_64)
    tarball=uv-x86_64-apple-darwin.tar.gz; tar_sha=7be2dcd316122f4b3e5a0a7889f6248266c14425374e105ff7dc9779184603a6
    wheel=packages/21/f2/0cb77a1ae43959772f8f7873eb1f4061907fbb1694ce55c79b231f639b1d/uv-0.13.4-py3-none-macosx_10_12_x86_64.whl
    wheel_sha=b4bbacea78c9db136cd2ec3147cbfd9ed910b4db5e1877109bd74d0e73bb2eaf ;;
  Darwin-arm64)
    tarball=uv-aarch64-apple-darwin.tar.gz; tar_sha=e0b1d4743ce64c55d9f0f4a444afa6b81125680070ee04e853fcd0f6c1ebf8f1
    wheel=packages/26/44/5a8733dd1b2e8d6f9cffd1ff6428a3e11dcc7e146a7657e32cfb8d9fb4a4/uv-0.13.4-py3-none-macosx_11_0_arm64.whl
    wheel_sha=95f4eb2b991b53b50ec2b18ab612fca8ba1e7a75a47af0331fb8bcd21da3eb03 ;;
  *) die "unsupported platform $(uname -s) $(uname -m); install uv yourself, then: uv tool install $source_spec" ;;
esac

fetch() {  # url file
  if command -v curl >/dev/null 2>&1; then curl -fsSL --retry 2 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then wget -q -O "$2" "$1"
  else die "neither curl nor wget is installed"; fi
}
reachable() {
  if command -v curl >/dev/null 2>&1; then curl -s -o /dev/null -I --max-time 4 "$1"
  else wget -q --spider -T 4 "$1"; fi
}
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
verified() {  # url sha file
  fetch "$1" "$3" && [ "$(sha256_of "$3")" = "$2" ] || { say "  $1: download failed or SHA-256 mismatch"; return 1; }
}

find_uv() {
  for c in "$(command -v uv 2>/dev/null || true)" "$vhome/runtimes/uv/uv" "$vhome/runtimes/uv/bin/uv" \
           "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if [ -n "$c" ] && [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
  done
  return 1
}

install_uv() {  # china_first
  dest=$vhome/runtimes/uv
  tmp=$(mktemp -d)
  mkdir -p "$dest"
  github=https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$tarball
  order="github pypi aliyun"
  [ "$1" = 1 ] && order="aliyun github pypi"
  for src in $order; do
    case $src in
      github)
        say "downloading uv $UV_VERSION from $github"
        if verified "$github" "$tar_sha" "$tmp/uv.tar.gz" && tar -xzf "$tmp/uv.tar.gz" -C "$tmp"; then
          cp "$tmp/${tarball%.tar.gz}/uv" "$tmp/${tarball%.tar.gz}/uvx" "$dest/"; break; fi ;;
      pypi|aliyun)
        base=https://files.pythonhosted.org; [ "$src" = aliyun ] && base=https://mirrors.aliyun.com/pypi
        say "downloading uv $UV_VERSION from $base"
        if verified "$base/$wheel" "$wheel_sha" "$tmp/uv.whl"; then
          if command -v unzip >/dev/null 2>&1; then unzip -q -o "$tmp/uv.whl" "uv-$UV_VERSION.data/scripts/*" -d "$tmp"
          else python3 -m zipfile -e "$tmp/uv.whl" "$tmp" >/dev/null 2>&1 || continue; fi
          cp "$tmp/uv-$UV_VERSION.data/scripts/uv" "$tmp/uv-$UV_VERSION.data/scripts/uvx" "$dest/"; break; fi ;;
    esac
  done
  rm -rf "$tmp"
  [ -f "$dest/uv" ] || die "could not download uv; install it (https://docs.astral.sh/uv/) and run this script again"
  chmod +x "$dest/uv" "$dest/uvx"
  printf '%s\n' "$dest/uv"
}

# 1. network
china=0
[ "$mirror" = cn ] && china=1
if [ "$china" = 0 ] && ! reachable https://huggingface.co/ && reachable https://www.modelscope.cn/; then
  china=1; say "huggingface.co is unreachable: using mirrors in mainland China (all downloads stay hash-checked)"
fi
if [ "$china" = 1 ]; then
  export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-$PYPI_MIRROR}"
  export UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-$PYTHON_MIRROR}"
fi

# 2. uv
if uv=$(find_uv); then say "using uv at $uv"; else uv=$(install_uv "$china"); fi

# 3. vidroute
case $source_spec in
  *://*) ;;
  *) [ -e "$source_spec" ] && source_spec=$(cd "$source_spec" && pwd) ;;
esac
say "installing vidroute from $source_spec"
"$uv" tool install --force --reinstall "$source_spec"
bin=$("$uv" tool dir --bin)
"$uv" tool update-shell >/dev/null 2>&1 || true
PATH="$bin:$PATH"
say "vidroute installed: $bin/vidroute"

# 4. setup
if [ "$setup" = 0 ]; then say "next: vidroute setup   (open a new shell first so PATH includes $bin)"; exit 0; fi
set -- setup
for h in $hosts; do set -- "$@" --host "$h"; done
[ "$china" = 1 ] && set -- "$@" --mirror cn
[ "$yes" = 1 ] && set -- "$@" --yes
code=0
if [ -t 1 ] && [ ! -t 0 ] && [ -r /dev/tty ]; then
  "$bin/vidroute" "$@" < /dev/tty || code=$?  # `curl | sh`: answer the setup question from the terminal
else
  "$bin/vidroute" "$@" || code=$?
fi
[ "$code" = 0 ] && say "done. Open a new shell to use 'vidroute' directly; restart your agent tools to load the skill."
exit "$code"
