# 文档导航

文档描述当前源码。发布镜像以对应 Release 为准，具体站点是否启用某功能由其配置决定；本地或离线测试不代表真实供应商生成与计费已验收。

## 安装与维护

- [发布镜像全量部署](deployment.md)：同一 Release 安装包与预构建镜像、建库/账号、Nginx、登录及排错。
- [源码构建部署](deployment-source.md)：开发提交、本地构建及源码实例更新，与发布镜像安装分开。
- [已有 New API 接入](existing-installation.md)：复用现有数据库/网络，安装 Cockpit 的配置与反向代理。
- [New API 兼容范围](compatibility.md)：数据库字段、JWT 登录接口及已核对的网关版本。
- [升级、备份与回退](upgrading.md)：迁移、密钥与数据备份，以及聊天存储的版本兼容。
- [版本发布与镜像](releasing.md)、[变更记录](../CHANGELOG.md)。

## 功能说明

- 统计：[统计口径](calculation.md)、[渠道质量](quality.md)、[余额监控](monitoring.md)、[通知渠道](notifications.md)。
- 管理：[用户与令牌](users.md)、[手工与定时配额](quota.md)、[登录与会话](authentication.md)。
- 模型工具：[模型使用教程](manual.md)、[智力测试](intelligence.md)、[生图工作站](image-studio.md)。
- [AI 助手](chat.md)：聊天、文件与联网、Skills / Memory、能力配置和隔离边界。
- 文档三个平级入口：[模型使用](manual.md)、[辅助工具与镜像内插件下载](tools.md)、[API 文档](interactive-api.md)；详细接口见 [API 参考](api.md)。
- [账户 MCP](mcp.md)：用户 PAT 认证、本人余额和用量、KEY 管理及接入配置。
- [VS Code 账户与 KEY 插件](../vsix/README.md)：独立 VSIX 安装、用户 PAT 配置、状态栏余额、本人 KEY 管理及缓存限流、Claude Code / Codex 待保存配置草稿。

## 开发与契约

- [贡献与验证](../CONTRIBUTING.md)：离线回归、隔离数据库、可选浏览器验证和打包。
- [整体架构](architecture.md)、[界面规范](ui.md)、[性能机制](performance.md)、[工作站源码维护](image-studio-sources.md)。
- [聊天基础契约](design/ai-assistant-v1/README.md)：HTTP、存储、运行限制、个人上下文和模块依赖方向。
- [聊天扩展契约](design/ai-assistant-v2/README.md)：三协议、受控联网、公开推理、自动记忆、自适应上下文及当前前端行为；与基础设计不同时以扩展契约为准。

具体服务器地址、账号、凭据、部署操作和一次性数据处理不写入公共文档，应保存在部署方管理的受保护记录中。
