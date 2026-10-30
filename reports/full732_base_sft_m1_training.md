# Full732 Base-SFT M1：本地准备PASS，远程训练BLOCKED

最后更新：2026-10-30。**本次没有训练出M1：短烟测、长烟测、正式训练及C244评估均未执行，实际optimizer steps为0。** 阻塞来自当前执行会话禁止创建网络socket，未连接远端；用户已说明主动开机，不将此错误解释成服务器关机或认证失败。

用户已确认最终Teacher数据可训练；本轮没有重做数据许可审查，没有读取或检查历史95条Teacher数据。用户最后补充“本轮需要进行C244评估”，按M1验收后执行固定C244理解，覆盖同一请求此前的禁评估句。C不进入训练loss、early stopping或checkpoint选择。Recovery-SFT/GRPO不在本轮范围。

## 源码与连接事实

GitHub连接器核对`codex/p0-p2-recovery`最新提交为`88836690c2ada2a14fc254a2f480ce2e44562773`。从其递归树同步缺失/落后的四份文档后，工作区637个受控文件逐一计算Git blob SHA，与该提交完全一致；私有数据未参与公开同步。

根工作区`.git`为空且只读，终端不是可用Git仓库。新临时目录的`git clone --single-branch --branch codex/p0-p2-recovery`失败，错误为`failed to open socket: Operation not permitted`。源码通过连接器读取，后续提交也使用连接器；不声称终端`git pull`已成功。

从`ssh.txt`读取既有连接信息，不打印或提交密码。首次SSH因`/etc/ssh/ssh_config.d/20-systemd-ssh-proxy.conf`所有权检查失败；使用`ssh -F /dev/null -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1`明确客户端配置后，两次均在创建socket时返回`Operation not permitted`，其中第二次是在用户报告已开机之后。尚未到认证，未核验远端路径、GPU型号、缓存或私有包，也未尝试绕过执行会话的网络限制。

## 本地数据验收

| 项目 | 本次实测 |
|---|---|
| 原始包 | `data/full732_teacher_successes_complete_20261029/` |
| 原始Manifest SHA256 | `96265f67a5828b101f5db32a804008478d7c419a01e079f6e468b8e5f164735f` |
| 原始归档SHA256 | `64c2a5854f0a3a9cfab72f71e13682dce5e535ff0b885768b98dc0073d6a7a48` |
| 合格候选 | 138任务/轨迹/场景，130 native + 8 compact-derived |
| Assistant样本/监督token | 2070 / 338413 |
| 屏蔽token/最长序列 | 22444500 / 32693 |
| 分词 | 固定Qwen3-4B revision、non-thinking模板，新进程完整重分词通过 |
| 相等检查 | 每条token IDs、labels、loss_mask、attention_mask、可见工具schema元数据与来源字段一致 |
| 角色隔离 | 源成功及合格任务全部属于冻结A244，B244/C244无交集 |
| 源包不可变 | 完成后再次核对全部包内文件、manifest及归档SHA均未变 |
| 暂缓 | 原6条继续deferred，无截断、删调用、改目标 |

沿用封存的`scripts/verify_teacher_inventory.py --retokenize`独立重读，校验包内全部文件和四个编码源码SHA。外置历史provider/DB证据保持原先封存引用，本轮不重新执行Teacher或官方评价。来源原始split保持不变；这是Full732自定义研究，不能当作官方train-only或排行榜结果。

固定模型revision为`1cfa9a7208912126459214e8b04321603b3df60c`，模板SHA为`a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`。本机只加载tokenizer，没有加载4B模型权重。

新增私有派生目录`runs/full732_m1_local_preparation_20261030/dataset/`；manifest SHA为`32c868d7035eb3bddf299f2e9c4e8220d1b6a5cd37a2b8b9945b477c6a5020aa`。它保留全部2070样本，唯一新增训练字段为`example_weight = 2070 / (138 * n_trajectory)`；每轨迹总权重约15，浮点范围14.999999999999993–15.00000000000001，样本平均1。再次流式比较派生行去掉weight后与原始行完全相等，未创建validation文件。

## 独立训练入口与软件验证

新增文件：

- `src/recoveragent/full732_sft_data.py`：可信归档/Manifest检查、固定A身份、完整重新分词、均衡权重及独立派生Manifest。
- `src/recoveragent/full732_sft_runtime.py`：复用原loss、collator、梯度检查、参数hash及BF16重载方法；`eval_strategy=no`、`eval_dataset=None`，拒绝调用`evaluate()`；原P3-B实现保持不变。
- `scripts/run_full732_m1_training.py`：远端环境与数据门槛→短烟测→最长样本烟测→正式训练，逐阶段独立进程/目录/日志，无重试。前序烟测回执和checkpoint文件SHA改变、失败或缺失均拒绝下一阶段。
- `configs/full732_m1_{short_smoke,long_smoke,formal}.json`及`configs/full732_m1_runtime_lock.json`：固定原LoRA r16/alpha32/dropout0.05、7投影模块、BF16/SDPA、学习率1e-4。正式batch1/accumulation8/1epoch；短烟测4完整短样本/2步，最长完整样本1步。65文件源码锁SHA为`d4b3941348a71570b0b093f929e1dac1869935a04c05777c8becefb7182c1959`。

runtime准备保存逐步loss/有限非零梯度、实际dataloader数量及全样本覆盖、初始/最终/重载LoRA哈希、同输入logits差、最终optimizer/scheduler/RNG/Trainer状态、GPU峰值及墙钟。正式预期`ceil(2070/8)=259`，必须以真实dataloader和Trainer状态验收；本轮实测仍为0。未生成虚构loss曲线或checkpoint。

软件结果：新增与相关测试43项通过；完整软件套件**558 passed in 10.14s**。相关4个Python文件Ruff check/format通过。测试覆盖mask/超长/漏回合/重复目标拒绝、轨迹均衡、B/C隔离、无Trainer validation、最长样本选择、烟测数据/配置/重载/产物变动拒绝，以及短烟测失败后不启动后续阶段。合成回执仅在临时软件fixture中，不算真实GPU烟测。

## 实际命令与未执行项

本地实际执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src CUDA_VISIBLE_DEVICES='' \
  HF_HOME="$PWD/.cache/huggingface" HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  .venv-inference/bin/python -c "from recoveragent.full732_sft_data import prepare; prepare('data/full732_teacher_successes_complete_20261029', 'data/full732_teacher_successes_complete_20261029.tar.gz', 'runs/full732_m1_local_preparation_20261030/dataset')"

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/ruff check --no-cache src/recoveragent/full732_sft_data.py src/recoveragent/full732_sft_runtime.py scripts/run_full732_m1_training.py tests/test_full732_sft.py
```

另执行流式原/派生样本相等检查、源包SHA复查及源码锁检查，结构化结果见同名JSON。本地私有tokenization日志位于派生目录，完整pytest输出保存在`/tmp/full732-m1-pytest.txt`。

以下仅为**未执行**的远端命令模板，路径变量须由实际SSH核验后设置，不代表机器已有这些路径或包：

```bash
PYTHONPATH=src "$training_python" scripts/run_full732_m1_training.py \
  --dataset "$verified_private_dataset" --archive "$verified_private_archive" \
  --model-cache "$verified_model_cache" --appworld-python "$appworld_python" \
  --output "$new_run_directory"
```

| 阶段 | 状态 | 实际optimizer steps |
|---|---|---:|
| 本地数据和软件准备 | PASS | 0 |
| 远端硬件/缓存/包/重新分词 | NOT_RUN | 0 |
| 最多2步短样本烟测 | NOT_RUN | 0 |
| 最长完整样本1步烟测 | NOT_RUN | 0 |
| 全量138候选单epoch | NOT_RUN | 0 |
| M1固定C244端到端评估 | NOT_RUN | 不适用 |

LoRA前后哈希、loss、显存峰值、训练耗时、checkpoint重载结果均为未产生，同名JSON使用null。模型推理和Teacher付费请求均0。不能据此声称已核验远端租机费用或关机；当前会话未向远端发出成功命令。

下一步是在支持SSH的执行会话核验用户已开机的既定实例和真实私有路径，再完成已授权的训练顺序。M1验收后，另将新checkpoint绑定完整冻结C244与现有execution_v6/动态发现/固定预算，完成独立评估；旧`evaluate_shared_stage.py`默认绑定C30，不能直接冒充C244。当前尚无M1，C244没有启动，也没有用旧checkpoint代替。
