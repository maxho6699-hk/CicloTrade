# CicloTrade 全站功能逻辑与因果链审计

日期：2026-08-18
工作树：`console-v2-visual-rework`
审计基线提交：`dcade8bc`（钴蓝主题与用户反馈修复）

## 1. 审计口径与实证

- 真实页面路由：25 个（另有旧路由重定向与 Workflow 详情动态路由）。
- 可渲染公共组件：`src/apps/web/src/components` 下 30 个 `.tsx` 文件；另有 3 个组件模型/辅助 `.ts` 文件。旧记录中的“52 个公共组件”不是当前仓库同口径数量，不能继续当作验收数字。
- 页面源代码：约 8,876 行。
- 前端合同测试：48 个测试文件，最新全量结果 261/261 通过。
- 自动交易专项 Python/HTTP 测试：88/88 通过（33 个与筛选条件无关的服务测试 deselected；仅第三方 backtrader 产生 2 条弃用警告）。
- 页面静态扫描：0 个 mock/fake/demoData 标记；0 个无处理器页面按钮（修复前发现页 Mini K 周期有 1 个伪按钮，已改为明确的状态标签）。
- 视觉与运行时证据沿用已提交批次：桌面 25/25、权限负向 2/2、平板/手机 50/50、用户反馈交互几何 25/25。
- 所有账户、行情、会员、推荐、券商、自动交易、AI 与任务数据均以 API/Workspace DTO 为边界；无真实数据时显示真实空态、锁定态或待接入，不用演示数填充。

## 2. 25 页因果链矩阵

| 页面 | 路由 | 主要输入 | 核心动作/输出 | 失败与空态边界 | 结论 |
|---|---|---|---|---|---|
| 首页 | `/` | 公共静态产品边界 | 登录/注册入口、能力说明 | 不请求账户私有数据 | 通过 |
| 登录 | `/login` | 会话、注册、邮箱验证、重置 API | 登录、注册、验证、重置后安全返回 | 错误不泄露原始后端细节；returnTo 白名单 | 通过 |
| 今日 | `/today` | Workspace 推荐、持仓、风险、Telegram | 进入发现、研究、通知 | 无正式事件时不造推荐；币种风险分开计算 | 通过 |
| 发现 | `/discover` | 推荐、Watchlist、市场状态、Mini K API | 条件筛选、选择股票、收藏、进入研究 | 无候选/无 K 线/读取失败均显式；1W/1M 标记待接入 | 已修复并通过 |
| 研究 | `/research` | Candles、quote、stream、alert、watchlist | 周期/分屏/画图、报警、研究证据 | 请求竞争隔离；陈旧/离线/无市场上下文 fail-closed | 通过 |
| 牛熊 | `/deliberation` | readiness、任务列表/详情、推荐绑定 | 创建、取消、重试、进入 Workflow | 无绑定资料时解释原因与下一步；无灰色空按钮 | 已修复并通过 |
| 个人模拟 | `/paper` | 模拟账户、报价、风险证明、订单 DTO | 风险复核、确认、幂等提交 | 七项风险门任一缺失即不交易；仅美股，不能自造股票 | 通过 |
| 组合复盘 | `/portfolio` | 官方模拟快照、持仓 | 进入研究/报告 | 可空字段保持 null；不发明模型绩效 | 通过 |
| 会员 | `/membership` | 计划/价格/购买动作/服务器 quote | 取得新 quote、创建订单 | 未取得当前 quote 不结账；未知响应复用幂等键 | 通过 |
| 推广 | `/promotion` | 钱包、佣金、提现、活动、审计 | 复制、提现、记录查询 | HKD minor unit；未知提现响应复用幂等键 | 通过 |
| 更多功能 | `/more` | 服务器 feature catalog、pin/recent | 搜索、固定、打开工具 | planned/locked/degraded 不可误操作 | 通过 |
| 通知 | `/notifications` | 通知 DTO、偏好、能力、服务端 deep-link | 已读、偏好、跳转 | deep-link 由服务端解析；缺能力不伪造投递 | 通过 |
| 账户 | `/account` | 外观、记忆、授权、账户限制 | 更新设置、授权开关、去券商页 | 授权策略/风险 DTO 不完整时 fail-closed | 通过 |
| 业绩 | `/earnings` | PIT 快照、统计、期权结构 | 浏览业绩概率与期权研究 | 权限、无数据和不可用状态分离；无假零绩效 | 通过 |
| 报告 | `/reports` | stable/expanded research | 选择 scope、下载/查看研究 | 无服务端报告不造本地 demo | 通过 |
| 实验室 | `/lab` | 队列、压力目录、CSV readiness/import | 运行压力、上传、下载、查看信号 | 目录 hash、256KB、所有者与结果完整性校验 | 通过 |
| 券商 | `/trade` | broker catalog/access、auto-live snapshot | 申请接入、mandate、确认、启动/暂停 | stale/unknown 阻止风险增加，安全退出保留 | 前端控制面通过；真实券商对账见第 4 节 |
| 全局 AI | `/ai` | readiness、session、task、events、answer | 创建会话、任务、取消、只读研究跳转 | 不显示 provider/model/token；没有订单提交工具 | 通过 |
| Workflow | `/workflow[/:taskId]` | owner-scoped task/event projection | 查看任务与事件、取消/重试 | 终态分类完整；任意 URL/原始 provenance 被拒绝 | 通过 |
| 推荐 | `/recommendations` | 正股/期权研究 DTO | 桌面抽屉/手机底部面板、进入研究/模拟 | 缺字段显式列出；不扩大卡片、不造组合价 | 已修复并通过 |
| 帮助 | `/help` | 静态知识 | Router 链接到真实页面 | 无异步依赖，不需要 loading/error 空态 | 通过 |
| 反馈 | `/feedback` | feedback list/submit API | 幂等提交、查看状态 | 只本地化已知状态；错误有可读说明 | 通过 |
| 娱乐 | `/mystic` | 服务器权限/记录 | 锁定或真实空态 | 无本地社交/玄学记录，不造数据 | 通过 |
| 法律 | `/legal` | 静态政策 | 政策入口 | 不谎称已有用户 consent receipt | 通过 |
| 管理员 | `/admin` | super_admin DTO、审核/财务/券商访问 | 高风险确认、人工审核、暂停/恢复 | 非 super_admin 重定向；秘密遮罩；模拟与生产边界 | 通过 |

## 3. 核心流程因果链（至少 20 条）

1. 会话刷新 → `ProtectedConsole` → 未认证跳登录并保留安全 returnTo。
2. 注册 → 邮箱验证 token → 账户激活；字段与状态严格校验。
3. 密码重置申请 → token 确认 → 旧会话失效。
4. Workspace 推荐 → 今日优先级 → 发现候选 → 研究路由（带 market/symbol）。
5. Watchlist 设置 → Workspace 刷新 → 发现/研究一致显示；跨市场同代码隔离。
6. K 线请求 → 请求序号/市场/周期 scope → 只接受当前响应。
7. 实时 forming bar → freshness/授权/延迟门 → 仅合格时显示实时。
8. 价格报警创建/停用 → 服务端 ID → 本地仅隐藏视觉 marker，不伪造删除。
9. 推荐事件 → deliberation binding → readiness → 创建任务 → Workflow task id。
10. 牛熊任务 → 取消/重试 → 幂等请求 → 终态与事件线更新。
11. 推荐卡 → 研究或模拟入口 → URL 上下文；AI/推荐不直接下单。
12. 模拟交易 seed → 当前报价 → 七项风险证明 → 用户确认 → 幂等订单。
13. 组合快照 → 持仓/结算字段 → 研究/报告 handoff；未知字段不推断。
14. 会员购买 → 当前服务器 quote → 二次校验 → pending order；未知响应不换 key。
15. 推广余额 → 提现金额 minor unit → 幂等申请 → 钱包/审计回执。
16. 通知项目 → 服务端解析 deep-link → 白名单路由；客户端不能替换 target。
17. 账户授权策略 → 风险文件/账户限制 → 券商申请；缺任一门则关闭。
18. Auto-live mandate → confirmation phrase → snapshot hash → active mandate。
19. Auto-live start → fencing epoch → worker lease → running receipt/heartbeat → open gate。
20. Auto-live pause → aggregate/broker/mandate scope → 回执 → partial/paused 区分。
21. Workflow artifact → 响应头/字节 hash 校验 → 允许下载；不一致即拒绝。
22. CSV 导入 → readiness → 上传 → 详情/信号；owner scope 与 256KB 门控。
23. 压力测试 → 服务器 catalog hash → scenario key → 任务；不允许任意输入扩大权限。
24. AI session → task receipt → events → structured answer；blocked receipt 永不长出答案。
25. 管理员高风险动作 → focus-trapped confirm → idempotency → 审计事件。

## 4. 自动交易专项结论

### 已存在且通过源码/合同的部分

- Mandate、确认短语、snapshot hash、有效期、撤销与 fencing epoch。
- start/pause 幂等请求和 append-only runtime receipt。
- worker lease、running ack、heartbeat freshness、projection hash 与 CAS。
- `submission_unknown` 会阻止 `open`，但继续允许 `cancel`、`reduce_exposure`、`close_position`。
- Shadow worker 先落不可变 intent/event，不持有 broker capability，不发送订单。
- Tiger 旧发送边界具备一次性 send claim、发送前重新校验、全局/用户/账户/风险总开关。
- Tiger 异常或本地状态无法确认时进入 `SUBMISSION_UNKNOWN`，写风险日志，禁止盲重试。
- `TIGER_REAL_TRADING_ENABLED` 默认关闭；不改变该生产安全状态。

### 真实缺口（需要实现）

- 新 Auto-live worker 目前模块说明明确为 **shadow-only**，没有 broker sender capability。
- `auto_live_order_intent_events` 虽预留 `send_claimed/accepted/rejected/submission_unknown/reconciled`，但仓库中只有 `shadowed` 写入路径。
- 旧 Tiger `orders()` 可以读取券商订单，但没有 owner-scoped reconciliation 服务把 `SUBMISSION_UNKNOWN` 与券商查询结果结算为 `SUBMITTED/FILLED/REJECTED/CANCELLED` 并写不可变 reconciliation receipt。
- 缺失自动对账时必须继续 fail-closed：未知状态不能重发，也不能开放新的风险增加动作。

因此，“broker receipt、SUBMISSION_UNKNOWN 恢复、reconciliation”待办仍成立；其余 Auto-live 控制面不是未完成，不应重复实现。

## 5. 已修复的审计发现

- 发现页 Mini K 周期原来把固定 1D 渲染成无处理器按钮，把 1W/1M 渲染成无原因 disabled button。
- 修复为非交互状态标签：`1D` 当前，`1W · 待接入`、`1M · 待接入`；CSS 支持换行。
- 新增回归合同，禁止该区域恢复为伪按钮。

## 6. 下一阶段范围

1. 设计并实现 broker reconciliation 适配器与不可变 receipt（先测试、默认无发送能力）。
2. 将 `SUBMISSION_UNKNOWN` 恢复绑定到 broker query，而不是重发。
3. 只在 accepted/filled/rejected/cancelled 的完整证据到达后更新 projection；模糊状态保持 unknown。
4. 运行自动交易专项 Python/HTTP 测试、全量前端测试和独立安全复审。
5. 不启用真实券商发送；生产总开关保持关闭。
