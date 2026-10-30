# Full732 Base-SFT M1：两次烟测PASS，正式训练进行中

最后更新：2026-10-30。网络已恢复，用户确认在当前实际A100-SXM4-80GB机器开始。远程数据复验和两次真实烟测通过，正式训练正在运行；目前尚未得到最终M1验收结果，C244尚未启动。前次网络阻塞记录独立保存在 `reports/full732_m1_network_blocked_20261030.md/json`，不能作为当前状态。

## 源码、设备与数据

- 分支 `codex/p0-p2-recovery` 包含指定 `88836690c2ada2a14fc254a2f480ce2e44562773`。网络恢复后临时clone/fetch成功；训练实际源码 `a6cae3569bf715b1e7854a286b9e2415f0a61702`，归档SHA `1d0732b05ea6fb54bf8036ba9c370d14eada1b2f9381cc83360da13c72aaef1e`。
- `ssh.txt`当前连接实际为A100 80GB，区别于原定PRO6000 96GB。用户经说明后确认开始。新增独立A100适配和锁 `76a33ff0ac5c8b6e06062d44607f1cad99d9ab12bc6ecc19eff12901a9ebed6a`，保留原96GB配置、原Full732锁及历史P3-B源码/权重。未新租机器、未读取历史95条数据。
- 原始manifest `96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f`；归档SHA `64c2a5854f0a3a9cfab72f71e13682dce5e535ff0b885768b98dc0073d6a7a48`。远程全包校验、重新分词、Assistant-only mask和A/B/C隔离通过：138候选、2070样本、338413监督token、22444500屏蔽token、最长32693。原包不改动，无截断、无调用删除、无Assistant目标修改。
- 固定原始 `Qwen/Qwen3-4B` revision `1cfa9a7208912126459214e8b04321603b3df60c`，模板SHA `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`，non-thinking；原始模型10文件哈希、BF16和固定训练/AppWorld依赖通过。LoRA r16/alpha32/dropout0.05、7投影、学习率1e-4不变。

## 真实训练门槛

| 阶段 | 状态 | 完整样本 | optimizer steps | 峰值allocated GiB | 重载logits最大误差 |
|---|---|---:|---:|---:|---:|
| 短烟测 | PASS | 4 | 2 | 8.849 | 0.0 |
| 最长样本烟测 | PASS | 1 | 1 | 23.159 | 0.0 |
| 全量1 epoch | RUNNING | 2070（目标） | 259（dataloader实际期望；完成数待验收） | 待完成 | 待完成 |

短烟测使用4完整短样本；长烟测使用32693 Token最长完整样本。两者梯度均有限且非零、LoRA参数真实变化，保存后重载参数hash完全相同，logits误差0。各次均从raw base重新初始化，正式M1不继承烟测参数。完整LoRA前后/重载哈希、配置、依赖、显存、耗时及文件SHA见 `reports/full732_m1_a100_smoke_evidence_20261030.json`。

正式训练为batch1/gradient accumulation8、1 epoch，真实dataloader为2070 batch，预期259优化步；训练尚未结束，不把预期填写为完成。每步保存loss和梯度检查，按既定间隔保存checkpoint；仅训练loss，无内部验证集、early stopping或C选checkpoint。私有完整日志和checkpoint位于远程独立 `recoveragent_full732_m1_20261030/runs/run_001/`。

## C244准备与边界

新增独立C244入口，复用原Student执行引擎，固定完整C244和原始split来源，execution_v6全文、动态发现、40请求/16384生成/4096单回复/32768上下文/600秒、seed42、temperature0.8、top_p0.95、Memory关闭。旧C30入口和历史源码锁不改；新源码锁绑定当前引擎，解决旧v6锁与后来adapter研究扩展不匹配的问题。后端仅接受经过正式训练及重载的全量M1，校验参数hash。

C244要等正式M1验收后才启动；当前0任务。它是自定义Full732已知场景开发评估，不是官方排行榜或未见场景泛化。未知保持null，不记失败0；完整244项均有效才给完整TGC。C不进入Trainer或checkpoint选择，不运行Recovery-SFT、GRPO或B侧采集。

软件验证：`PYTHONPATH=src:. .venv/bin/pytest -q` 为 **571 passed in 9.90s**；新增训练A100/C244文件Ruff通过。初次全套测试因命令仅设`PYTHONPATH=src`导致5个`scripts`导入错误，修正测试启动路径后完整通过，无源码修补掩盖失败。

待完成：正式259步及最终保存/重载验收、训练曲线与完整证据回传、固定C244评估、最终报告和状态提交。任何实际训练门槛失败则停止并保留日志。
