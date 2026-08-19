#!/bin/sh
set -eu

BASE_URL="${ORA_INSTALL_BASE_URL:-https://recruit.agentmesh360.com}"
ADAPTER_VERSION="0.1.13"
ADAPTER_SHA256="4d8c10b1497776ac213eb25a2928971ee92ab8c8b7a5c2dd5b7f489c2757d60b"
SKILL_VERSION="0.3.8"
SKILL_SHA256="45cbce03d86dce71fb308722a601f6484fb22eeac08228435be2e9c83bb942e6"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_ROOT="${ORA_AGENT_HOME:-$HOME/.agentmesh360/official-recruitment}"
RELEASE_ROOT="$INSTALL_ROOT/releases/$ADAPTER_VERSION"
VENV="$RELEASE_ROOT/venv"
BIN_DIR="${ORA_BIN_DIR:-$HOME/.local/bin}"
SKILLS_ROOT="${ORA_SKILLS_DIR:-$HOME/.agents/skills}"
SKILL_DIR="$SKILLS_ROOT/agentmesh-officialrecruitment"
WORK_DIR="$(mktemp -d)"
WHEEL="$WORK_DIR/official_recruitment_agent-$ADAPTER_VERSION-py3-none-any.whl"
SKILL_FILE="$WORK_DIR/SKILL.md"
INSTALL_FINALIZED=0
RELEASE_REPLACED=0
PREVIOUS_RELEASE="$INSTALL_ROOT/update/previous-$ADAPTER_VERSION-$$"

cleanup() {
  status=$?
  trap - EXIT INT TERM
  if [ "$status" -ne 0 ] && [ "$INSTALL_FINALIZED" -eq 0 ]; then
    if [ "$RELEASE_REPLACED" -eq 1 ]; then
      rm -rf "$RELEASE_ROOT"
      if [ -d "$PREVIOUS_RELEASE" ]; then
        mv "$PREVIOUS_RELEASE" "$RELEASE_ROOT"
      fi
    fi
  fi
  rm -rf "$PREVIOUS_RELEASE"
  rm -rf "$WORK_DIR"
  exit "$status"
}
trap cleanup EXIT INT TERM

verify_asset() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY'
import hashlib
from pathlib import Path
import sys

path = Path(sys.argv[1])
expected = sys.argv[2]
if expected.startswith("__ORA_"):
    raise SystemExit("安装器尚未绑定正式资产摘要，安装已停止。")
actual = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"安装资产校验失败：{path.name}")
PY
}

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "需要 Python 3.11 或更高版本。")'
mkdir -p "$INSTALL_ROOT/releases" "$INSTALL_ROOT/update" "$BIN_DIR"
chmod 0700 "$INSTALL_ROOT" "$INSTALL_ROOT/update" 2>/dev/null || true
curl -fsSL "$BASE_URL/downloads/official-recruitment-agent.whl" -o "$WHEEL"
curl -fsSL \
  "$BASE_URL/downloads/agentmesh-officialrecruitment-skill/SKILL.md" \
  -o "$SKILL_FILE"
verify_asset "$WHEEL" "$ADAPTER_SHA256"
verify_asset "$SKILL_FILE" "$SKILL_SHA256"

if ! grep -q '^name: agentmesh-officialrecruitment$' "$SKILL_FILE"; then
  printf '%s\n' "Skill 文件校验失败，安装已停止。" >&2
  exit 1
fi
if ! grep -q "^version: $SKILL_VERSION$" "$SKILL_FILE"; then
  printf '%s\n' "Skill 版本与安装器不一致，安装已停止。" >&2
  exit 1
fi

CLI="$VENV/bin/ora-workbench"
NATIVE_HOST="$VENV/bin/ora-native-host"
if [ ! -x "$CLI" ] || [ "$($CLI --version 2>/dev/null || true)" != "ora-workbench $ADAPTER_VERSION" ]; then
  if [ -d "$RELEASE_ROOT" ]; then
    mv "$RELEASE_ROOT" "$PREVIOUS_RELEASE"
  fi
  RELEASE_REPLACED=1
  mkdir -p "$RELEASE_ROOT"
  "$PYTHON_BIN" -m venv "$VENV"
  "$VENV/bin/python" -m pip install \
    --disable-pip-version-check \
    --upgrade \
    --force-reinstall \
    "$WHEEL"
fi
if [ "$($CLI --version)" != "ora-workbench $ADAPTER_VERSION" ]; then
  printf '%s\n' "CLI 版本检查失败，安装已停止。" >&2
  exit 1
fi
ORA_SKIP_UPDATE=1 "$CLI" install-finalize \
  --skill-file "$SKILL_FILE" > "$WORK_DIR/install-finalize.json"
INSTALL_FINALIZED=1
"$PYTHON_BIN" - "$WORK_DIR/install-finalize.json" \
  "$ADAPTER_VERSION" "$SKILL_VERSION" <<'PY'
import json
from pathlib import Path
import sys

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if report.get("status") != "ready":
    raise SystemExit("客户端安装兼容检查失败。")
if report.get("client_version") != sys.argv[2]:
    raise SystemExit("客户端安装版本不一致。")
if report.get("skill_version") != sys.argv[3]:
    raise SystemExit("Skill 安装版本不一致。")
if report.get("local_profile", {}).get("status") != "ready":
    raise SystemExit("本机资料库迁移检查失败。")
PY
rm -rf "$PREVIOUS_RELEASE"

cat > "$INSTALL_ROOT/launcher.py" <<'PY'
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
try:
    current = json.loads((root / "current.json").read_text(encoding="utf-8"))
    cli = Path(current["cli_path"]).resolve()
except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
    raise SystemExit("受管客户端版本指针无效，请重新运行官网安装器。") from error
if root not in cli.parents or not cli.is_file():
    raise SystemExit("受管客户端入口不在官方安装目录，请重新运行官网安装器。")
os.execv(str(cli), [str(cli), *sys.argv[1:]])
PY
chmod 0755 "$INSTALL_ROOT/launcher.py"
ln -sfn "$INSTALL_ROOT/launcher.py" "$BIN_DIR/ora-workbench"

printf '%s\n' \
  "AgentMesh-OfficialRecruitment Skill 与 CLI 适配器已安装。" \
  "版本：${ADAPTER_VERSION}；Skill：${SKILL_VERSION}" \
  "Skill：$SKILL_DIR/SKILL.md" \
  "CLI：$BIN_DIR/ora-workbench" \
  "Chrome 本机连接组件：已注册" \
  "已有 API Key、本机画像和扩展配对资料均已保留。" \
  "请重新打开宿主 Agent 任务，让它读取新 Skill。" \
  "首次使用前由你本人配置通用 API Key：" \
  "ora-workbench configure --key <AGENTMESH_API_KEY>"
