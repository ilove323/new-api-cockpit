# Security

维护者：[@ilove323](https://github.com/ilove323)。
维护最新发布版本；修复经开发分支与 PR 合并后发布。

请勿在公开 Issue 中提交密码、令牌、数据库备份、客户数据或可直接利用的漏洞详情。
若仓库已启用 GitHub 私密漏洞报告，请使用仓库 Security 页的 Report a vulnerability。
若该入口不可用，请先提交不含漏洞细节的 Issue，请维护者提供私密报告方式。

源库普通查询使用只读事务；受限函数仅能补建缺失 PAT。业务修改经 New API 官方接口执行，监控连接只指向独立监控库。
网页登录复用 New API 会话，必须使用 HTTPS；Cookie 默认 Secure、HttpOnly、SameSite=Strict，访问凭据不进入 URL 或浏览器持久存储。
会话、角色与账号状态交给 New API 每次验证；不读取密码哈希、不共享签名密钥、不直接读写 Redis。
密码、二次验证码仅发送给同源官方登录接口；不得降级绕过二次验证。妥善备份通知加密密钥及监控库。
`/healthz` 检查网页进程与已配置定时器的就绪状态，不证明所有源库查询或远端操作可成功。
