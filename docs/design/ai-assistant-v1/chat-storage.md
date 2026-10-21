# 文件存储、隔离、幂等与恢复

> 本文描述当前源码契约，验证入口见[开发验证](../../../CONTRIBUTING.md#ai-助手离线回归)。只支持单宿主机的本地 POSIX 文件系统；禁止多主机副本共享 NAS/NFS 后声称锁和持久化仍然成立。

## 1. 目录与信任边界

Compose 所在目录的 `./data/chat` 绑定至 `/app/data/chat`，只有 chat 容器可读写；web/image 不挂载。
不能用未挂载的容器层，也不能把匿名 volume 描述成“部署目录下的 data”。

```text
/app/data/chat/<CHAT_INSTANCE_ID>/
  storage.json                         # v=1、实例标识；不含连接凭据
  usage.json                           # 派生额度台账，可重建
  reservations/<operation_id>.json      # 在途空间预留，不含正文/KEY
  .locks/
    storage.lock                       # 短期空间/目录提交锁
    startup.lock
    slots/0.lock、1.lock
    users/<user_id>.run.lock            # 跨进程每用户最多1轮
    users/<user_id>.profile.lock        # 个人设置一致性锁
    conversations/<user_id>.<cid>.lock  # 会话单写者锁
  users/<user_id>/
    index.json                         # 派生会话列表，不是唯一事实源
    context-manifest.json              # 个人设置的权威提交点，不能当派生索引重建
    profiles/{MEMORY,SOUL,IDENTITY}/versions/<n>.md
    skills/<sid>/versions/<n>/SKILL.md  # 本人Skill；物理名不是用户输入name
    .context-tmp/、.context-quarantine/ # 计个人与总额度，按个人上下文规则恢复
    conversations/<cid>/
      meta.json                        # 事件投影，原子替换
      events.jsonl                     # 顺序、追加、版本化事件
      commit.json                      # 已提交seq/字节偏移；只读可见性高水位
      files.json                       # 已提交文件事件的派生映射
      runs.json                        # 幂等ID/状态投影，不是凭据库
      contexts/<rid>.json              # 本轮个人设置/Skill冻结快照
      contexts/<uuid>.working.json     # 不可变工作窗口/检查点，受提交引用与哈希保护
      uploads/<fid>.blob               # 原件只读，物理名由服务器控制
      outputs/<fid>.blob               # 不可变输出版本
      control/<rid>.cancel.json        # 停止标志，只对对应rid有效
      .tmp/                            # 同文件系统临时文件
      .quarantine/                     # 待修复非完整/孤立文件，仍计额度
    deleted/<cid>.json                 # 极小删除墓碑，禁止旧ID复活
    .trash/<cid>/                      # 已从列表移除、待清理的删除目录
```

`CHAT_INSTANCE_ID` 是运维配置的稳定非秘密标识，不接受 HTTP 参数。禁止每次启动生成新 ID，否则历史会“消失”。
个人设置完整manifest/版本/清理规则以[个人上下文](user-context.md)为准：仅手工 API 或本轮显式自动 Memory 经同一受控 CAS 组件更新当前 manifest；工具输出同名MEMORY.md也不能覆盖它。
同一 New API 用户更名、换 KEY、重新登录不能改变 `<user_id>`；更换 New API 数据库/站点时须用不同 instance ID，避免不同站点恰巧同数字 ID 串数据。
不自动把一个用户被删除后另建的账号认作原账号；如外部系统复用数字 ID，运维必须在复用前隔离该旧用户目录，不能靠用户名猜测。

客户端只操作 cid/fid/rid；后端从已验证 Principal 计算 owner 路径。任何请求均不接受绝对路径、相对目录、用户名或 user_id 来指定 owner。
一条请求的授权范围为 `(instance_id, principal.user_id, cid)`；所有下级索引查找都在这个范围内。

## 2. 权限与路径安全

- 根目录预先由运维创建并归容器 UID/GID 10001，目录 0700、文件 0600、umask 0077。不得 chmod 777 解决权限问题。
- 打开根/用户/会话目录使用目录 fd + `openat`/`dir_fd`，各层 `O_NOFOLLOW`、检查目录/普通文件类型；blob 禁止软链接，且拒绝异常 hardlink（nlink != 1）。仅 `Path.resolve().is_relative_to()` 不足以消除竞态。
- UUID、用户数值、配置 instance_id 分别严格校验；拒绝 `..`、斜杠、反斜杠、NUL、编码穿越和设备文件。展示文件名绝不作为物理路径。
- 各工具仅收到当前会话 `Workspace`，不是全局根路径。MEMORY/身份/Skill 由有权限的上下文组件显式读入，不向四个文件工具暴露用户设置目录。
- 随机 UUID 防碰撞但不是授权；每次列表、详情、事件、状态、取消、文件下载、设置与 Skill 都复核 owner。
- 应用层超级管理员也不能旁路。宿主机管理员/备份操作者是另一信任边界，不能承诺磁盘明文不可见。
- 聊天内容不进访问日志、报错 traceback 局部变量或操作记录表；日志只记 code/时长/受控 ID。平台获取的原始 KEY、PAT、Cookie、Authorization 绝不进磁盘。

## 3. 事件格式与数据字典

UTF-8，无 BOM，一行一个紧凑 JSON，对象以 LF 结束；不使用 gzip/zstd，便于首版定位与恢复。
seq 从 1 严格连续递增，v 固定 1；未知主版本拒绝写入，不能猜字段继续追加。
同一日志只含一个 conversation_id，首条事件声明 owner/instance 并与实际路径及 Principal 交叉检查。

```json
{"v":1,"seq":1,"type":"conversation.created","conversation_id":"11111111-1111-4111-8111-111111111111","run_id":null,"at":"2026-10-15T02:00:00Z","data":{"owner_user_id":42,"instance_id":"site-a","title":"新聊天"}}
```

通用字段：v/seq/type/conversation_id/run_id/at/data，全部必需。at 为服务端时间，排序以 seq 而非时间为准。
下表保留可兼容读取的首版事件；当前新增 reasoning/协议状态/候选选择/检查点/Memory 审计和可选字段见[扩展契约](../ai-assistant-v2/contracts.md)。所有事件仍严格白名单，除显式 nullable 外不填未知 null。

| type | data 字段与语义 |
|---|---|
| `conversation.created` | owner_user_id:int、instance_id:str、title:str；只允许 seq=1 |
| `conversation.renamed` | title:str；手动改名或首轮本地标题 |
| `conversation.skills.updated` | skill_ids:uuid[]，有序唯一最多 3，run_id=null；仅空闲聊天可更新，持久投影为 loaded_skill_ids，旧历史默认 []；不修改已接受运行的冻结快照 |
| `file.added` | file:FileSummary；物理 blob 已同步落盘后才能提交 |
| `run.accepted` | client_request_id:uuid、message_id:uuid、text:str、attachment_ids:uuid[]、token_id:int、model:str、mode:chat/files、capability_revision:str、context_snapshot:{file:str,sha256:str,context_revision:int,skill_ids:uuid[]}、context_from_run_ids:uuid[]；本轮用户消息与幂等承诺同时落盘 |
| `run.dispatching` | request_index:int、assistant_message_id:uuid；**每次**收费 HTTP 发起前同步写入，表示已跨不确定边界 |
| `assistant.delta` | message_id:uuid、text:str；合并后有界文本增量，先持久化再对外发送 |
| `assistant.message` | message_id:uuid、text:str、tool_calls:ToolCall[]、finish_reason:str、complete:bool；覆盖该 message_id 的增量投影，不重复计文本 |
| `tool.call` | call_id:str、name:str、arguments:object；JSON结构完整、有界的调用，schema合法才执行；不可用任意参数决定宿主路径/权限 |
| `tool.result` | call_id:str、name:str、ok:bool、result:object；失败时 result={code,message}，不暴露底层错误 |
| `run.usage` | request_index:int、prompt_tokens:int、completion_tokens:int、total_tokens:int；只有上游明确给出完整三字段且非负时写入 |
| `run.terminal` | status:completed/failed/cancelled/interrupted、reason:str、error_code:str/null、uncertain:bool、model_requests:int、tool_calls:int、usage:Usage/null、usage_complete:bool |

ToolCall=`{id:str,name:str,arguments:object}`；发送给上游时转换成该协议要求的 function.arguments JSON 字符串，而不是原样照搬内部结构。
`context_snapshot.file` 仅允许由 rid 推导的 `contexts/<rid>.json`，不是外部可控路径；API 可返回这个内部相对标识，但不允许按它直接下载。
Usage=`{prompt_tokens,completion_tokens,total_tokens}`，为有有效 usage 的 request_index 去重后求和；usage_complete 仅当每次模型请求都报告完整用量时 true。
保存的是业务投影，不保存上游 SSE 原文、request header、模型 KEY 或任意字段。平台不会自动提取用户正文中的秘密；用户主动输入的正文仍按聊天内容保存，界面要提醒勿粘贴凭据。

### 3.1 一轮最短有效序列

```text
run.accepted（包含用户消息）
run.dispatching(request_index=1)
assistant.delta × N
assistant.message(complete=true, tool_calls=[])
run.usage（可没有）
run.terminal(status=completed)
```

工具轮次：assistant.message(tool_calls 非空) → 逐个 tool.call/tool.result → 下一 run.dispatching。
不能把未解析完整的流式 tool arguments 交给执行器；被拒绝的调用仍用有界失败结果记录。
参数JSON不完整、非object或ID缺失属于协议错误，直接结束本轮；JSON完整但工具名/schema不合法可记录安全工具失败结果，不能调用任意函数。
跨请求的 tool call ID 不能重复；同一轮 run.dispatching 的 request_index 从1连续递增。
取消/失败时若完整assistant.message会超剩余事件预算，可不补重复全文快照，直接写terminal；投影根据已保存delta展示complete=false的部分回答。

### 3.2 派生文件

`meta.json`：v/owner_user_id/instance_id 加 API conversation 字段；revision=last_seq。`files.json`、`runs.json` 含 v、last_seq、各自 entries。
`commit.json={v:1,seq:int,byte_offset:int}` 是活跃写入期间的已提交高水位；在events fsync之后原子替换并同步父目录，之后才可对外发布。
`runs.json` 可存规范化载荷 sha256、client_request_id→rid、终态、用量；不存 KEY、session、credential。
所有投影都可由有效事件前缀重建，不能为了节省扫描而把它们变成第二个不可恢复的事实源。
不可变附件/输出/上下文快照 + JSONL 才构成完整可恢复记录；备份不能只取 meta.json。
只读接口先读commit高水位；发现投影last_seq落后、file_id/run_id未命中时，须从高水位以内日志前缀补投影再返回；活动写锁在用时可仅在内存补，不等待整轮或把已提交结果误报404。
例如SSE已报告file.added而files.json尚未替换，随即下载也应通过完整事件找到已同步blob。
健康的普通GET只读已发布前缀和当前投影，不拿会话排他锁、不重写相同索引；避免页面并发读取历史、文件时阻塞随后发送/上传。只有坏尾、未完成run或投影不一致才尝试惰性恢复；孤立文件清理安排在启动恢复或下一写者取得租约时，普通GET不承担目录维护。

## 4. 写入与空间预留

任何持久化失败必须先停止派发后续模型请求，不能退回“只在内存继续聊”。

### 4.1 事件与元数据

1. 外层用例取得 ConversationLease 并验证有效前缀，计算下一 seq；内部append/write helper接收该已持有租约，绝不重新open/flock同一锁，也不替外层提前释放。
2. 严格 schema 与序列化大小检查；为终态保留 EVENT_TERMINAL_RESERVE_BYTES。
3. 追加完整行并循环处理短写，`flush + fsync`；随后按临时文件→fsync→replace→目录fsync发布commit高水位，两者均成功才可SSE输出。
4. 生成新投影，写同目录临时文件 → fsync → `os.replace` → fsync 父目录；其last_seq不能超过commit高水位。
5. 若事件和commit已成功而仅投影更新失败，不回滚事件、不再次执行动作；恢复时由日志重建。

append、events fsync或commit发布任一步失败，写者进入poisoned状态，关闭上游、拒绝所有后续append（包括finally补terminal）。
只有持会话锁的恢复器验证/修复尾部并重新fsync、发布commit后才允许写；不得把terminal接到半行后面制造永久坏记录。
仅投影失败不等于日志poisoned，可以据commit重建，但不得忽略存储故障继续无限收费。

delta 在 DELTA_FLUSH_MS 或 DELTA_FLUSH_BYTES 触发批次，批次只形成一个有界事件。已显示文本保证对应已同步事件；意外掉电仍受存储设备实际持久性保证约束。

### 4.2 文件与上下文快照

1. 使用外层已持ConversationLease；申请/使用空间预留；只使用当前 owner/cid 的目录 fd。独立上传由外层拿锁，运行工具复用本轮租约。
2. 写 `.tmp/<uuid>`，检查 UTF-8/类型/大小/sha256，fsync。
3. 原子移动到唯一、不可覆盖的 blob/snapshot 名，fsync 目录。
4. 追加 file.added 或引用快照的 run.accepted；事件提交前不向浏览器返回“成功”。
5. 释放多余预留，更新派生索引。

崩溃在第3/4步之间可能出现孤立 blob/snapshot，不能自动拼成用户消息或自动发模型；恢复将其隔离到 .quarantine、计额度并记录告警。
write_file 同一 logical_name 的版本号由会话写锁内递增，返回新的 fid；绝不覆盖上传文件或既有版本。
`(rid, call_id)` 是工具写入幂等键，已提交 file.added 则返回原版本，不生成重复文件。

### 4.3 配额账本

空间限制见运行表。计算必须包含事件、投影、附件、输出、上下文快照、个人配置/Skill、临时/隔离文件和在途 reservations，不只计算正文。
每次运行预留 RUN_STORAGE_RESERVE_BYTES；上传按请求上限加元数据余量预留；个人设置更新按新文件+临时文件上界预留。
新建、改名、恢复等非运行元数据写入也必须先预留本次事件、commit与受影响投影的序列化字节及原子替换临时副本峰值，检查会话/用户/全站三层限额；不能绕过配额直接append。
每运行预留另覆盖一个小取消控制文件及其原子临时副本，停止请求不受“用户已用满”阻碍；宿主被其他进程写满仍可能导致真实I/O失败，不能假报取消已持久化。
`usage.json` 是缓存；首次启动/异常恢复在 storage.lock 下核对实际磁盘、恢复 reservation，不能因为缓存损坏把占用当成0。
reservation 文件原子保存 `{v,operation_id,user_id,conversation_id|null,kind,bytes,created_at}`，活动状态以对应文件锁为准，不以 PID 或时间猜测。
释放/回收失效 reservation 前必须证明其会话/profile 写锁可取得；不能删其他 worker 仍在用的预留。
空间预留锁只保护配额和短提交，不在 HTTP/模型/长文件读取期间持有。运行内写入扣自己的预留；超出则终止，不无限扩容。
磁盘剩余低于 DISK_FREE_MIN_BYTES 或数据根不可写：拒绝新运行/上传；已有历史仍尽可能只读可见，不把磁盘失败当空列表。

## 5. 锁与并发

使用 Linux `fcntl.flock(LOCK_EX|LOCK_NB)`，锁 fd 设置 close-on-exec。锁文件是固定 inode，不因解锁/会话删除而 unlink，否则可形成两把不同的“同名锁”。
不能只用 Python dict/Lock、进程 PID 或前端 disabled 按钮承担多进程互斥。

获取顺序（只拿操作需要的子集，不能逆序）：

1. 用户运行锁（付费 run 才拿）。
2. 任意一个全局 slot 锁（付费 run 才拿）。
3. 会话写锁（运行全程、上传/改名/删除短操作）。
4. 用户 profile 锁（冻结或管理 MEMORY/身份/Skill 时短持有）。
5. storage 配额锁（短持有）。

只有最短期的storage配额锁允许在本机有界等待 `STORAGE_LOCK_WAIT_SECONDS=0.5` 秒；用户/slot/会话/profile锁仍非阻塞。此等待不是模型请求排队或网络重试，超时明确失败，不重复收费。

运行启动同步拿前三项，所有预检/落盘成功后所有权转给本轮生产线程；只有生产线程结束才释放，不能 SSE 消费者断开就提前放出新收费容量。
同一用户不同会话不能并行生成；不同用户可竞争全局2槽（配置可调）。会话写锁拒绝修改该正在运行的会话，其他聊天可读/改名/上传。
只读历史不等待整个运行：读取原子commit高水位、meta/索引及高水位以内日志；有LF但尚未发布commit的行不可见。
必要修复若拿不到锁则只显示已发布前缀，不能误判active run已崩溃。commit缺失/坏损且写者活跃时返回503等待，不能自行猜最后完整行已提交。

取消是例外：鉴权和核对 rid 后原子写其专用 cancel 文件，不抢运行长期持有的会话锁。标志只能单向设 true、重复幂等；终态已存在则只返回终态。
旧 rid 的 cancel 文件永远不能取消新 rid。执行者检查取消，但停止不撤销上游已计费部分。
本机运行表的注册、取消和清理统一使用 `(instance_id, user_id, cid, rid)`，不能把UUID当成跨作用域授权键；即便历史导入或存储异常造成rid重复，也不能取消/覆盖其他用户或聊天的活动运行。稳定用户ID不包含session或用户名，同一用户重新登录仍可停止自己的原轮次。

## 6. 幂等与“不自动重复收费”

规范化摘要覆盖 text、attachment_ids 顺序、token_id、model、mode、capability_revision、context_revision、skill_ids 顺序；使用 UTF-8 紧凑、排序键 JSON 的 SHA256。
expected_revision、当前 session/JWT、时间、服务端生成 rid 不参与；仅换登录会话不应把同一请求当新运行。
`client_request_id` 唯一于当前 owner/cid。幂等映射由 run.accepted 重建，与聊天保留期相同；不能使用几分钟 TTL 导致旧按钮再次收费。

处理顺序：鉴权 → 查现有幂等 → 相同返回原 run/冲突拒绝 → 获取锁 → 再查幂等 → 校验 expected_revision/模型/设置版本 → 冻结上下文/保存 accepted → 开始执行。
重复请求命中时不重新应用当前个人设置，不覆盖历史的上下文快照。
不承诺网络意义上的 exactly-once：上游可能在本地断线前已经收费。因此每次 `run.dispatching` 先 fsync，失败后标为 uncertain，不自动再发同一请求。
即使知道发送可能尚未到达上游，也保守禁止自动收费重试；用户核对后可明确点击新一轮，使用新的 client_request_id。

## 7. 崩溃恢复

启动只初始化 chat 存储/恢复器，不运行数据库 DDL 或任何模型调用。多个 worker 用 startup.lock 串行初始化；不能以进程重启清空状态。
读取时也允许对当前 owner 下缺失投影做惰性恢复；以下写恢复必须拿到会话锁。

| 场景 | 行为 |
|---|---|
| 末行没有 LF、写入中断 | 在无活动写者时保存坏尾到 .quarantine，截断到上一完整行并 fsync；重建投影 |
| 中间行 JSON 损坏、seq 不连续、owner/cid 不一致、完整行 schema 错误 | 标记 corrupt，停止在最后可信前缀；禁止继续写，不静默丢弃中间记录 |
| meta/files/runs/index 缺失或版本落后 | 由事件/目录重建，保留该聊天可见性 |
| commit缺失/滞后，且已取得会话锁证明无写者 | 验证完整前缀、修复坏尾并对events重新fsync，原子发布新commit，再重建投影；不把这个离线恢复规则用于活跃GET |
| accepted/running 无 terminal，且能取得其运行/会话锁 | 追加 interrupted；有 dispatching 则 uncertain=true，无则 false；绝不续跑模型/工具 |
| file.added 引用的 blob 缺失/哈希错误 | 文件标记不可下载，聊天仍可查看；阻止依赖它的新运行，提示修复 |
| 未提交临时/孤立文件 | 隔离、计空间、报有限诊断；不当作成功上传/输出 |
| 有已终态 run 的旧 cancel | 可在会话锁下清理，清理不改变事件历史 |

如果另一个 worker 仍持有写锁，不能仅因 meta 的 updated_at 旧或 PID 不认识而宣布中断。
当文件系统写满，连 terminal 也写不出时保留可验证前缀、对外失败/断流；空间恢复后根据锁和 dispatching 记录恢复为 interrupted，不能伪称已持久化 cancelled。
损坏的首行无法恢复可信标题时，目录仍作为“记录待修复 <cid短码>”展示给该目录所属用户；不读取其他 owner 内容来补全。

## 8. 列表与“每个聊天都可看见”

每次列举要核对本人 conversations 目录中合法 UUID 子目录集合，不能只读可能过期的 index.json。
只读取该用户，不递归全站会话内容。单人数量受 CONVERSATIONS_PER_USER 上限限制，首版不需要数据库索引。
在创建/删除/修复时原子更新本人索引；列举发现遗漏就补条目。一个损坏聊天不应让整个列表返回失败或假空。
日志事件按 seq 分页直到指定高水位；不把 model 上下文截断等同于删除 UI 历史。
处于 failed/cancelled/interrupted 的用户消息、部分回答和文件继续可查看；只有用户明确删除才从列表移除。
个人 MEMORY/SOUL/IDENTITY 和 Skill 有意在**本人不同聊天间**共享；普通聊天附件和输出不跨聊天共享。两种作用域不能混为一个搜索目录。

## 9. 删除、备份与恢复

删除在会话锁下核对 revision：

1. 原子把 conversations/cid 改名为本人 `.trash/cid`，同步父目录；此时停止列举。
2. 原子写本人 deleted/cid.json（只含 v/cid/deleted_at），更新索引。
3. 安全递归删除 trash 内容，不跟随链接；释放实际占用。清理失败保留墓碑，下次启动重试，不让会话复活。

创建前检查墓碑和 trash；崩溃在1/2之间，启动补墓碑并继续清理。运行中禁止删除。
用户设置/Skill 不随某个聊天删除；删除聊天时删除其 contexts 快照与文件。删除个人 Skill 不修改已经保存的历史运行快照。

备份推荐停止 chat 写入后备份整个 `data/chat`，或使用一致文件系统快照；不要对正在运行的多文件状态做普通复制后宣称一致。
恢复时保留 UID/GID/权限、CHAT_INSTANCE_ID 与正确 New API 用户 ID 映射；重新跑离线恢复检查，不能自动续跑未完成请求。
备份含用户正文和个人 MEMORY；运维需要相同访问保护和保留/删除策略。聊天不新增数据库迁移，也不改变 New API 自身可能保存的请求日志。
