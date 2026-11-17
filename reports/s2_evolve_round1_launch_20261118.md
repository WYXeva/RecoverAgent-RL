# 新Judge协议下一轮Evolve启动记录

截至2026-11-18 00:28（UTC+8），正式远端作业已启动，S0基线已完成1/24、获官方评分成功，第二题执行中。尚无训练组或optimizer更新。本文是启动记录，不是完整评价或学习效果报告。

## 冻结范围

- 用户要求在Judge复验后开始S2、自主处理运行意外；沿用此前“一轮Evolve＋dev24、最多24小时”。不启动Static、test、SFT或第二轮。
- 现有L40S 48GB；使用已验收Qwen3.5-9B S0，64K/non-thinking/BF16/LoRA/vLLM。固定train100、dev24和单题预算不变。
- 当前协议S0 dev24 → 40组×4次Student → 每两组一次GRPO更新机会 → 轮末dev24。最多20次更新机会，无有效奖励差异则不更新。旧S0历史成绩保留，未更改旧评价绑定冒充本协议成绩。
- CoEvolve继续后台生成，每8组最多4个context/8候选；采用已修复的共享Flash/thinking enabled/high/8192 Judge，Solver准入无外部参考，Student带同题同初态参考，证据不足为无效奖励。
- 正式启动：2026-11-18 00:23:24；绝对截止：2026-11-19 00:23:24。先完成则先结束。到期由监督器保存已有记录、停止所属服务；不自动租机或续费，不声称平台实例会自动关机。

## 初始池与真实准备

旧9题只读复用完整Solver证据。刚完成固定复验的Venmo候选与准备候选逐字段一致，直接复用该同协议准入原件；其余8题各调用一次新Judge。最终8题接纳、1题未接纳，不重跑Solver、不补生成凑足9题。

未接纳的订阅分摊题：Solver按35.00请求；Judge依据三位室友及已有46.67付款记录认为应为46.67。原判断及金额分歧完整保留，没有为了扩大池改判；该题本轮不训练，空缺生成槽由原始题补位。

新增8次准入调用：133,851输入/13,107输出token，206.94秒；项目保守估价$0.055733172。复用的1次不计新调用。Teacher预算仍独立：初始池最多9次；本轮Student生成题奖励最多80次；生成端使用当前独立有限预算与共同24小时截止，不以诊断额度代替正式运行额度。

已在远端验证：

- 执行源码提交 `292ab360df67d27c986e3a2478b1eb7ede3f4f31`，执行版 `v2_s2_judge_reference_20261117`。源码锁校验通过；本次没有修改运行源码或重签历史锁。
- S0文件/模型身份/原重载回执与冻结配置一致，adapter参数SHA `5da6444aa979e2ce0957b3bba82bd239ba0f59fad40024a7ea0a35b7f4534966`。
- 初始池协议、8份准入证明及100份train来源反馈通过读取/哈希校验；Teacher凭据、AppWorld和既有训练环境可用。任务与参考数据仅存私有目录。
- vLLM 0.19.0真实连通：短请求、缓存一致性、适配器与原基座控制差异、两条65,520输入token的完整窗口合成请求均通过。随后开始第一道真实dev任务；这些检查不算训练轨迹或任务成功。
- 沿用上轮180项针对性回归结果；本次为已验收代码部署，没有新学习实现需要额外模型实验。硬件启动时GPU空闲、数据盘约37GB可用。

## 监督、备份与路径

远端根目录：`/root/gpufree-data/recoveragent_s2_evolve_round1_20261118_01/`。

- `deployment.json`：实际启动时间、24小时截止、S0/环境路径与用户范围；重启不能重置截止。
- `source/`：独立冻结执行源码；`initial_pool/`：本协议共同初始池。
- `run/supervision/`：Student/Teacher日志、进程退出/有限续跑事件；`supervisor.json`：当前进度。
- `run/baseline/`、`run/evolve/`：新基线、真实组、奖励、反馈生成、更新与轮末评价。`run/evolve/summary.json`、`summary.md`由既有汇总器随进度生成。
- 本地 `runs/s2_evolve_round1_20261118_01/remote_backup/`：5分钟增量备份，包含回执、归档采样证据、运行状态及已保存checkpoint；`backup_watch.log`记录传输状态。备份不控制训练，网络断开后继续重试。正在写入的文件下次再取，原始采样请求以正式归档备份。

监督器独立于SSH运行。可恢复的进程/服务故障按现有规则最多三次自动续跑，已执行轨迹不重复消费；OOM按用户既有要求跳过并留证。身份/hash/非有限梯度等不能安全继续的问题停止并保存日志，不能伪造有效结果。Teacher等待新的8组反馈，Student不等待任务生成完成。

## 启动与续跑

已经启动，不要并行再启动一份。若监督器已退出、当批截止仍有效，可在远端复用原部署续跑：

```bash
cd /root/gpufree-data/recoveragent_s2_evolve_round1_20261118_01/source
export PYTHONPATH="$PWD/src:$PWD"
export RECOVERAGENT_S2_EXECUTION=v2_s2_judge_reference_20261117
export RECOVERAGENT_TEACHER_TOKENIZER_DIR=/root/gpufree-data/recoveragent_s2_evolve_round1_20261117_01/teacher_tokenizer
export HF_HOME=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/hf_cache
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
nohup /root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python -u \
  scripts/supervise_s2_background.py \
  --deployment ../deployment.json --output ../run \
  > ../resume.log 2>&1 < /dev/null &
```

既有监督器依据 `experiment.json` 自动选择run/resume，沿用actor/optimizer、采样队列、已处理窗口与回执。新协议状态不能混入旧目录；截止到期后不能靠该命令延长授权。

轮末评价由当前调度器自动执行；此处不调用 `evaluate` 子命令，因为该命令用于完整五轮后的最终test。待本轮实际完成后，再根据同条件dev24配对结果判断是否有增量。明早若仍在运行，应报告实际进度，不把启动或部分组数写成完成。
