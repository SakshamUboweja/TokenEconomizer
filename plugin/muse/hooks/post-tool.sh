#!/bin/sh
# TokenEconomizer's PostToolUse hook for Muse Code (plugin/.muse-plugin/plugin.json). Muse names each
# hook's script in its manifest and refuses two hooks with the same one, hence one per event.
# It sets MUSE_PLUGIN_ROOT for plugin hooks; the fallback is this file's own place in the plugin.
root=${MUSE_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/../.." 2>/dev/null && pwd)}
# The installed plugin is a read-only copy, where Python cannot cache its compiled modules.
[ -n "${HOME:-}" ] && export PYTHONPYCACHEPREFIX="$HOME/.cache/tokeneconomizer/pycache"
# Muse kills a hook whose stderr passes 16 KiB; the plugin's own errors go to its errors.log.
if [ -f "$root/scripts/te-hook" ]; then
  exec sh "$root/scripts/te-hook" --host muse 2>/dev/null
fi
while IFS= read -r _line; do :; done
echo '{}'
