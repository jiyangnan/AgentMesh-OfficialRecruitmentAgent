# 宿主 Agent 使用流程

1. 运行 `ora-workbench doctor`，确认产品服务和当前结构化档案状态。
2. 若缺少档案，只读取用户明确指定的标准简历。
3. 运行 `ora-workbench profile-schema`，按返回契约生成结构化字段。
4. 运行 `ora-workbench propose-profile-import` 创建待用户在 Web 审阅的档案提案。
5. 用户自行打开招聘官网当前页面；通过 Chrome 扩展识别和辅助填写当前步骤。
6. 用户亲自处理歧义字段、敏感字段、声明、附件、CAPTCHA 与最终提交。
7. 使用 `ora-workbench list applications` 和 `ora-workbench application <id>` 查看长期状态。
8. 对线下面试、笔试等外部进展，只能基于用户提供的证据创建
   `ora-workbench propose-transition` 提案，等待 Web 审阅。

`ora-workbench` 是确定性的 CLI 适配器，不是另一个 Agent。私有产品服务和数据库是
长期业务状态的权威来源。
