#!/bin/sh
set -eu

BASE_URL="${ORA_INSTALL_BASE_URL:-https://recruit.agentmesh360.com}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_ROOT="${ORA_AGENT_HOME:-$HOME/.agentmesh360/official-recruitment}"
VENV="$INSTALL_ROOT/venv"
BIN_DIR="${ORA_BIN_DIR:-$HOME/.local/bin}"
SKILLS_ROOT="${ORA_SKILLS_DIR:-$HOME/.agents/skills}"
SKILL_DIR="$SKILLS_ROOT/agentmesh-officialrecruitment"
WORK_DIR="$(mktemp -d)"
WHEEL="$WORK_DIR/official_recruitment_agent-0.1.2-py3-none-any.whl"
SKILL_FILE="$WORK_DIR/SKILL.md"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT INT TERM

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "需要 Python 3.11 或更高版本。")'
mkdir -p "$INSTALL_ROOT" "$BIN_DIR"
curl -fsSL "$BASE_URL/downloads/official-recruitment-agent.whl" -o "$WHEEL"
curl -fsSL \
  "$BASE_URL/downloads/agentmesh-officialrecruitment-skill/SKILL.md" \
  -o "$SKILL_FILE"

if ! grep -q '^name: agentmesh-officialrecruitment$' "$SKILL_FILE"; then
  printf '%s\n' "Skill 文件校验失败，安装已停止。" >&2
  exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install \
  --disable-pip-version-check \
  --upgrade \
  --force-reinstall \
  "$WHEEL"
ln -sf "$VENV/bin/ora-workbench" "$BIN_DIR/ora-workbench"

mkdir -p "$SKILL_DIR"
cp "$SKILL_FILE" "$SKILL_DIR/SKILL.md"
chmod 0644 "$SKILL_DIR/SKILL.md"

link_host_skill() {
  host_root="$1"
  if [ ! -d "$host_root" ]; then
    return
  fi
  mkdir -p "$host_root/skills"
  target="$host_root/skills/agentmesh-officialrecruitment"
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    printf '%s\n' "保留已有宿主 Skill：$target"
    return
  fi
  ln -sfn "$SKILL_DIR" "$target"
}

link_host_skill "$HOME/.codex"
link_host_skill "$HOME/.claude"
if [ -d "$HOME/.openclaw/workspace" ]; then
  link_host_skill "$HOME/.openclaw/workspace"
fi

printf '%s\n' \
  "AgentMesh-OfficialRecruitment Skill 与 CLI 适配器已安装。" \
  "Skill：$SKILL_DIR/SKILL.md" \
  "CLI：$BIN_DIR/ora-workbench" \
  "请重新打开宿主 Agent 任务，让它读取新 Skill。" \
  "首次使用前由你本人配置通用 API Key：" \
  "ora-workbench configure --key <AGENTMESH_API_KEY>"
