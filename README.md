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

macOS / Linux：

```bash
curl -fsSL https://recruit.agentmesh360.com/install-agent.sh | sh
```

Windows PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://recruit.agentmesh360.com/install-agent.ps1 | iex"
```

这条命令安装的是 **Skill 与 CLI 适配器**，不会安装或创建另一个 AI Agent。

安装后，由用户本人配置 AgentMesh360 通用 API Key：

```bash
ora-workbench configure --key <AGENTMESH_API_KEY>
ora-workbench doctor
```

API Key 可在 [AgentMesh360 个人中心](https://agentmesh360.com/account/) 获取。

## Chrome 扩展

产品提供两个官方安装渠道：

1. 能够访问 Chrome Web Store 的用户，优先从官方商店页面安装并由 Chrome 自动更新；
2. 无法访问商店的中国大陆用户，从工作台下载官方 ZIP，解压后在
   `chrome://extensions/` 开启开发者模式并选择“加载已解压的扩展程序”。

[Chrome Web Store 官方扩展](https://chromewebstore.google.com/detail/agentmesh-officialrecruit/fbgfhigphgmacnhgeomdjemfomhnjaai)
已经公开可安装。ZIP 下载、当前版本、SHA-256 和完整中文步骤见
[官方安装指南](https://recruit.agentmesh360.com/guides/install-browser-extension/)。两条
渠道使用同一源码、官方扩展 ID 和连接协议；商店更新可能因 Google 审核短暂晚于官方
ZIP，但不会形成权限或安全能力不同的地区版本。

本机 Agent 也可以代为校验并准备 ZIP：

```bash
ora-workbench extension prepare
```

命令会从 AgentMesh360 官方域名读取版本清单，校验扩展 ZIP 的大小与 SHA-256，解压到
当前系统的固定用户目录，并尝试打开该目录和 Chrome 扩展管理页。macOS、Windows 和
Linux 使用同一条命令。Chrome 首次加载仍由用户本人确认。

扩展安装后只需点击一次“连接本机 Agent”。安装器注册的本机连接组件只允许固定官方
扩展 ID 调用，并由本机 Agent 使用用户此前配置的通用 API Key 完成账户校验；扩展既
不展示 API Key 输入框，也不读取、保存或要求用户再次复制通用 API Key。本机配对秘密
同样不会返回扩展。

后续可以由宿主 Agent 运行：

```bash
ora-workbench extension status
ora-workbench extension update
ora-workbench extension repair
```

更新始终使用同一个固定目录，完成后用户只需在扩展管理页点击“重新加载”。用户自行打开
目标招聘官网和具体表单，再点击扩展中的“识别当前步骤”；本产品不要求系统预先穷举并
定位所有招聘页面。

扩展会先展示当前步骤的完整填写预览。用户逐项核对后，必须勾选“我已核对以上预览”，
“确认填写”才会变为可用。扩展只把经过页面稳定回读的字段记为成功；页面拒绝的值会
恢复原状并明确提示。填写后仍可选择“撤销本次填写”，招聘网站的保存、下一步、附件、
验证码、声明和最终提交始终由用户本人处理。弹窗底部的“前往工作台”会打开当前已验证
连接对应的工作台，不携带 API Key 或招聘页面参数。

## 待补资料的本机交接

招聘页面出现标准简历没有覆盖的问题时，云端工作台可以集中展示并让用户填写。宿主
Agent 会自行运行：

```bash
ora-workbench profile-handoff start
```

工作台只向产品服务申请不含答案的短时凭证；答案由浏览器直接交给本机 CLI，并在用户
复核后保存在本机资料库。AgentMesh360 云端不接收这些答案。Chrome 扩展随后只读取当前
填写任务需要的本机字段，不会取得整份资料库。

## 数据盘点与删除

用户可以在 Web 工作台“数据管理”中查看来源、机会、申请、档案、Agent 提案和活动记录，
逐条选择需要删除的记录。宿主 Agent 也可以使用同一套服务合同：

```bash
ora-workbench data inventory
ora-workbench data delete-preview --item sources:<source-id>
```

宿主 Agent 必须先向用户说明当前数据、连带影响和不会受到影响的账户权益，并等待用户针对
当前预览明确要求删除，才能运行 `data delete-confirm`。不得把一条记录扩张成整个类别或
其他用户的数据。删除预览绑定账户、精确记录清单、数据快照和一次
性确认码；跨账户、过期、数据已变化或重复执行都有确定的拒绝或回执结果。

若确认删除时返回申请辅助计费尚未核对，宿主 Agent 会先说明情况，并在用户明确要求继续
后使用当前预览运行 `data reconcile-billing`。该动作只核对原扣费：未扣费时阻止同一请求
稍后补扣，已扣费但未交付时先退还对应 Credit，不会直接删除工作台数据。核对完成后必须
重新盘点、生成新预览，并再次取得用户针对新预览的明确删除指令。

删除本产品工作台数据不会删除 AgentMesh360 账户、API Key、通行证、共享 Credit 余额或
Core 账本，也不会自动退款。本机补充资料由用户自己的本机 Agent 单独管理，原始简历文件
仍由用户自行保管。

## 安全边界

- Cookie、密码和登录态留在用户浏览器中；
- 原始简历文件、正文、文件名和本机路径不上传产品服务；
- 工作台待补资料的草稿留在浏览器，确认后的答案留在本机资料库；
- 本机交接只监听固定的 `127.0.0.1` 地址，并校验工作台来源、账户和当前任务；
- 生产扩展只保留 AgentMesh360 官方 HTTPS 域名和固定的 `127.0.0.1:8765` 本机交接
  权限，不包含开发工作台端口、局域网地址或任意招聘网站的持久主机权限；
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

## License

Apache License 2.0。
