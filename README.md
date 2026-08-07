# CicloTrade · Quant Research Terminal

面向普通投资者的多用户金融研究、回测、风控与交易工作台。系统默认使用模拟交易；当前仓库可供本地验收，但在下列外部阻塞项完成前，不应视为可公开收费运营的生产系统。

## 本地启动

```powershell
cd C:\Users\maxho\Desktop\TradeAI
py -3 -m pip install -r requirements.txt
py -3 -m streamlit run asgi_app.py --server.port 8501
```

打开 `http://localhost:8501`。`asgi_app.py` 是完整入口，包含 Streamlit UI、`/api/health`、专业版 API、支付回调路由、安全响应头和可选后台任务；不要直接用 `app.py` 代替它。

## 已实现（本地代码）

- 正式环境邮箱验证、bcrypt 密码、JWT Access/Refresh Token、单会话登录、失败限流、IP 记录与密码重设
- 5 档订阅、月/季/年周期、条款版本留痕、订单、Paddle、PayPal、FPS，以及支付平台逆转的幂等权益回滚
- 美股与 A 股查询、自选列表、Yahoo K 线、成交量、EMA20/50、VWAP、RSI、ATR、价格分布与相关矩阵
- 基于行情与成交量规则生成的研究候选、正股操作参考、期权结构参考及明确风险线
- 8 种期权策略、Backtrader 回测、参数遍历、历史记录与 Excel 导出
- 模拟盘订单、账户/平台暂停开仓、统一风控、一键全平、多账户登记及后台审计
- Tiger、Telegram 三层群组与个人推送、品牌邮件/消息模板、SMTP 接口，以及推荐奖励、续费提醒和客服/财务/研究后台
- 繁体中文默认界面、简体浏览器语言转换、桌面/移动布局和基础键盘/减少动态效果支持

外部接口“已有代码”不等于“已通过真实商户、券商或消息通道验收”。缺少凭证时相关路径会保持关闭或报出明确配置错误。

## 已测试范围

自动化测试覆盖认证与会话隔离、订阅条款、支付逆转原子性和幂等、PayPal 回跳捕获校验、FPS 后台确认、推荐/分享奖励、订单风控、模拟账户账本、预警任务、8 种策略、行情指标、选股搜索及后台权限。可在本机重跑：

```powershell
python -m compileall -q .
python -m pytest -q
python -m pip check
```

这些测试使用本地数据库和模拟的外部响应，不代表 Paddle、PayPal、Tiger、SMTP、Telegram 或商业行情的正式环境验收。

## 行情与代理边界

- 默认 `yfinance` 是第三方研究数据，可能延迟、修订、限流或暂时不可用；它不是具备交易所商业分发授权和 SLA 的实时行情源。
- Yahoo 不提供 Level 2 深度盘口。当前界面不会生成虚假订单簿；实时盘口、历史期权和更完整的期权数据需另接商业数据源。
- 当前 Polygon 适配器只读取约 100 天的日线聚合数据；分钟线、搜索、期权链和 Level 2 尚未由该适配器实现。
- A 股行情及美股期权链当前仍经 Yahoo 获取；A 股个股期权链和 A 股实盘下单尚未接入。研究候选是可解释的技术规则输出，不是获利保证或自动执行指令。
- `TRUST_PROXY_HEADERS=false` 时应用不会信任外部转发 IP，本地看到的客户端地址会是回环地址。只有在受信反向代理会清除并重写转发头时才可设为 `true`，否则 IP 限制可被伪造。

## 正式上线阻塞项

以下项目需要外部账号、授权或主体资料，无法仅靠本地代码完成：

- Paddle 商户 KYC、正式 API Key、9 个 Price ID、Webhook Secret，以及沙盒和正式回调端到端验证
- PayPal 商户凭证、Client ID/Secret、Webhook ID，以及正式支付逆转和争议流程验证
- Tiger 实盘只保留管理员联调入口；用户端继续关闭，待独立券商账户隔离完成后再开放
- Resend 域名验证完成后的 SMTP 投递验收，以及 Telegram Bot 在三个群中的管理员发信权限
- 美股/A 股商业实时行情、Level 2、历史期权等相应的数据许可、API 与再分发授权
- 正式域名、DNS、HTTPS、受信反向代理，以及支付/密码重设公开回调地址
- 公司主体、收款资料、不退款政策，以及用户协议、隐私、风险披露、投资建议边界和目标市场合规审阅

部署配置以 `.env.example` 为准。上线前必须替换所有示例密钥，保持 `.env` 和 `.streamlit/secrets.toml` 不入库，并对数据库备份、监控与恢复流程做单独验收。
