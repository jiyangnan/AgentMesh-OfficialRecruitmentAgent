# AgentMesh-OfficialRecruitment

本仓库是 `AgentMesh-OfficialRecruitmentAgent` 的公开分发仓，面向用户原本就在使用的
Codex、Claude Code、OpenClaw 等宿主 Agent。

它交付三项本机能力：

1. `agentmesh-officialrecruitment` Skill：告诉宿主 Agent 如何解析用户明确指定的标准
   简历、如何调用产品服务，以及何时必须停止并让用户介入；
2. `ora-workbench` CLI 适配器：在宿主 Agent 与私有产品服务之间传递结构化请求，并在
   用户电脑上运行私密资料交接与本机资料库；
3. Chrome 扩展：在用户已经打开的招聘官网当前步骤中识别字段、辅助填写，并回传有
   边界的证据。

长期申请数据、状态时间线、Web 工作台、云端业务数据库、身份校验和服务端策略不在本
仓库，由私有产品服务负责。仅用户确认的待补资料保存在本机资料库中。

## 一句话安装

```bash
curl -fsSL https://recruit.agentmesh360.com/install-agent.sh | sh
```

这条命令安装的是 **Skill 与 CLI 适配器**，不会安装或创建另一个 AI Agent。

安装后，由用户本人配置 AgentMesh360 通用 API Key：

```bash
ora-workbench configure --key <AGENTMESH_API_KEY>
ora-workbench doctor
```

API Key 可在 [AgentMesh360 个人中心](https://agentmesh360.com/account/) 获取。

## Chrome 扩展

当前种子阶段使用开发者模式安装：

```bash
ora-workbench extension-setup
```

命令会返回扩展包下载地址与图文安装说明。用户自行打开目标招聘官网和具体表单，
再点击扩展中的“识别当前步骤”或“辅助填写当前步骤”；本产品不要求系统预先穷举并
定位所有招聘页面。

## 待补资料的本机交接

招聘页面出现标准简历没有覆盖的问题时，云端工作台可以集中展示并让用户填写。宿主
Agent 会自行运行：

```bash
ora-workbench profile-handoff start
```

工作台只向产品服务申请不含答案的短时凭证；答案由浏览器直接交给本机 CLI，并在用户
复核后保存在本机资料库。AgentMesh360 云端不接收这些答案。Chrome 扩展随后只读取当前
填写任务需要的本机字段，不会取得整份资料库。

## 安全边界

- Cookie、密码和登录态留在用户浏览器中；
- 原始简历文件、正文、文件名和本机路径不上传产品服务；
- 工作台待补资料的草稿留在浏览器，确认后的答案留在本机资料库；
- 本机交接只监听固定的 `127.0.0.1` 地址，并校验工作台来源、账户和当前任务；
- 扩展不绕过 CAPTCHA，不自动勾选声明，不自动上传文件，也不自动提交申请；
- 敏感字段、歧义字段、声明、文件选择和最终提交必须由用户处理；
- 扩展识别或填写不等于申请已提交；只有明确的用户操作和证据才能更新长期状态。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
python scripts/package_extension.py \
  --source extension \
  --output /tmp/agentmesh-officialrecruitment-extension.zip \
  --production
```

## 仓库关系

- 本仓库：公开客户端分发，仅包含 Skill、CLI 适配器、本机私密资料交接与资料库、
  Chrome 扩展和用户文档；
- `official-recruitment-agent`：私有内部研发源仓，先实现和验证完整产品能力；
- `official-recruitment-server`：私有产品服务、Web、数据库、状态机和部署；
- `agentmesh-core`：AgentMesh360 共享账号、订阅、积分、通用 API Key 与产品注册。

## License

Apache License 2.0。
