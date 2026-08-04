# Agent 协作约束

## 仓库定位

这是 `AgentMesh-OfficialRecruitmentAgent` 的公开分发仓。产品显示名是
`AgentMesh-OfficialRecruitment`。

本仓库只负责：

- 宿主 Agent Skill；
- `ora-workbench` CLI 适配器；
- 本机私密资料交接与本机资料库；
- Chrome 扩展；
- 安装器、公开协议和用户文档。

## 禁止进入本仓库的内容

- Web 工作台源代码；
- API、数据库模型、迁移和部署配置；
- 服务端策略、内部提示词、反滥用规则和运维手册；
- 内部进展、实验原始证据和私有语料；
- API Key、令牌、Cookie、个人简历、真实用户记录或招聘数据。

这些能力分别属于私有内部源仓、私有服务仓或 AgentMesh Core。

## 产品边界

- 用户继续使用自己的 Codex、Claude Code 或 OpenClaw；本产品不会安装另一个 Agent。
- 一句话安装只安装 Skill 和 CLI 适配器。
- 用户自行打开招聘官网当前页面，扩展只处理当前可见步骤。
- 不绕过 CAPTCHA，不自动勾选声明，不自动上传文件，不自动提交申请。
- 原始简历正文、文件名和本机路径不得上传服务端或写入日志。
- 云端工作台待补资料的答案必须直接交给本机 CLI，只能写入本机资料库；不得增加云端
  答案回退路径。
- 本机回环服务必须固定监听 `127.0.0.1:8765`，校验 Host、Origin、账户、当前任务和
  API Key，不得扩大为任意本机端口或任意网页来源。
- CLI 和扩展只调用公开、版本化的产品协议，不实现私有服务端策略。

## 开发验证

```bash
python -m pip install -e ".[dev]"
pytest
python scripts/package_extension.py \
  --source extension \
  --output /tmp/agentmesh-officialrecruitment-extension.zip \
  --production
```

提交前必须确认禁止路径不存在，并检查新增内容不包含密钥或真实用户资料。
