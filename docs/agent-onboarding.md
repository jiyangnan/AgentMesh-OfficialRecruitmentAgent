# 宿主 Agent 使用流程

1. 运行 `ora-workbench doctor`，确认产品服务和当前结构化档案状态。
2. 运行 `ora-workbench profile-handoff start`，确保云端工作台可以把待补资料直接交给
   本机资料库；该动作由宿主 Agent 执行，不要求用户管理终端进程。
3. 若缺少档案，只读取用户明确指定的标准简历。
4. 运行 `ora-workbench profile-schema`，按返回契约生成结构化字段。
5. 运行 `ora-workbench propose-profile-import` 创建待用户在 Web 审阅的档案提案。
6. 用户确认首份档案后运行 `ora-workbench profile-foundation`。若存在常用资料缺口，引导
   用户进入 Web“个人档案 -> 补全常用资料”；不得在聊天中索取敏感答案，也不得把建议项
   解释为招聘页面阻塞项。
7. 用户自行打开招聘官网当前页面；通过 Chrome 扩展识别当前步骤，逐项核对完整预览，
   勾选“我已核对以上预览”后再确认填写。识别本身不得修改页面。
8. 若出现标准简历没有覆盖的问题，必须让用户在工作台完整填写并核对本机提案；未收到
   本机确认前不能声称资料已经保存。
9. 扩展只把通过页面稳定回读的字段报告为成功；出现未可靠写入时必须让用户复核，不能
   声称当前步骤已经填好。用户可以撤销本轮写入。
10. 用户亲自处理声明、附件、CAPTCHA、下一步与最终提交。
11. 使用 `ora-workbench list applications` 和 `ora-workbench application <id>` 查看长期状态。
12. 对线下面试、笔试等外部进展，只能基于用户提供的证据创建
   `ora-workbench propose-transition` 提案，等待 Web 审阅。

`ora-workbench` 是确定性的 CLI 适配器，不是另一个 Agent。私有产品服务和数据库是
长期业务状态的权威来源；用户确认的待补资料答案以本机资料库为准，不进入云端业务
数据库。
