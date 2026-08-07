# 高阶策略上传模板

新电脑只负责运行策略并把结果推送到 CicloTrade 服务器。服务器负责验证、保存连续账本、生成页面和 Telegram 通知。

1. 在新电脑设置与服务器相同的 `TRADEAI_STRATEGY_INGEST_TOKEN`，长度至少 32 位；不要写入代码或 Git。
2. 每次策略操作生成唯一 `external_event_id`。网络重试必须复用同一个 ID 和完全相同的内容。
3. 推送操作事件：

```powershell
python -m strategy_client.push event strategy_client/event.example.json
```

4. 每次计算完账户净值后推送快照：

```powershell
python -m strategy_client.push snapshot strategy_client/snapshot.example.json
```

生产环境固定使用 `https://ciclotrade.com`。同一个 ID 如果内容不同，服务器会拒绝，避免重试时篡改历史。更正或撤销旧操作时使用 `event_type=correction` 或 `reversal`，并填写旧事件的 `corrects_event_id`；不可直接覆盖旧记录。
