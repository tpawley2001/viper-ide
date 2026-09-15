#!/usr/bin/env bash
# Dev launcher. Hosts without a system libGL can drop the libs (extracted from
# the libgl1/libglx0/libegl1/libxkbcommon debs) into .gl-libs/ and Qt picks them up.
here="$(cd "$(dirname "$0")" && pwd)"
gl="${VIPER_GL_LIBS:-$here/.gl-libs/usr/lib/x86_64-linux-gnu}"
[ -d "$gl" ] && export LD_LIBRARY_PATH="$gl${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$here/.venv/bin/python" "$@"
