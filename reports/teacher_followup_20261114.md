# 检查点恢复失败后的从头示范补采

状态：11月14日11:25（UTC+8）已完成7题、14次真实尝试，0新增合格示范、0服务异常；仍在继续冻结45题补采。原18条恢复后缀保留。

用户要求：Teacher不能从检查点恢复时改从头执行；DeepSeek仍不行则改GPT，密钥和网址不变。原S1已训练完成，这次补采独立保存，不改写原63次恢复、18条合格后缀、S1权重或dev24结果。

## 输入和尝试规则

来源是固定train100反馈后的63次真实恢复。原统计：18成功；27声明完成但官方失败、15上下文耗尽、2请求预算耗尽、1HTTP400服务异常。18条成功全部编码合格，169个Assistant样本、24,688监督token。失败不等于基础模型不具备能力：从Student失败状态接续还受既有副作用和长历史影响。

从原名单顺序冻结未合格的45个train task ID，不加入dev/test，也不新增任务。来源`recovery_summary.json` SHA：`32f08144eb4145603f0874c2d54c733319d4955ecd37ef37a00cc84b8c0bc1f8`。

每题先`deepseek-v4.1-flash`从原始初态执行一次；未得到合格示范时，`gpt-6-luna`从同一原始初态独立执行一次。最多90次新增模型/任务尝试；原尝试和新尝试分别保留。沿用65,536上下文、80模型请求、16,384累计生成token、4,096单回复、1,200秒，官方验收以原任务起点为参照。工具动态发现、nullable v2、execution_v6及严格Harness校验均复用。

**从头成功是完整Teacher示范，不是恢复后缀。** 复用原示范过滤、Qwen3.5-9B固定tokenizer/non-thinking模板和Assistant-only编码；工具/用户/控制信息不监督，超长整条deferred，不截断。旧18条后缀仍只监督Teacher接续部分。新增数据尚未参与当前S1参数更新。

## GPT接口与证据

沿用`https://opencode.ai/inference/go/openai/v1`及`DEEPSEEK_API_KEY`。该账号的`/models`实际返回`gpt-6-luna`。[OpenCode Go官方接口表](https://opencode.ai/docs/console/go/)列出GPT使用`/responses`，DeepSeek使用`/chat/completions`；基础网址和账户未变。

`teacher_responses.py`将Responses原生函数调用/工具返回接到现有Teacher循环，保留实际wire request及未修改的原始response对象。模型名、response ID、缓存/输入/输出用量可核验；推理信息不作为Assistant训练目标。工具参数Schema逐项保留，Responses的`strict:false`避免服务自动将可选参数改成必填；本地Harness继续严格验证原Schema。导出时重建实际请求并检查响应映射，不能凭构造的成功摘要准入。

独立原生两轮探针：HTTP200×2、响应模型`gpt-6-luna`、工具调用与结果回传通过；随后通过本次TeacherPolicy再次完成两轮真实接口探针。共4个合成探针请求，不是AppWorld任务成功。使用账户既有权限，没有改订阅、启用额外账单或充值。

## 实现与软件验证

- 独立入口：`scripts/collect_teacher_followup.py`，复用`p3a.run_teacher_episode`、官方隔离评分、`teacher_collection.process_attempt/save_sample`及既有SFT掩码。
- 独立配置/源码锁：`configs/teacher_followup_20261114{,_lock}.json`，源码基线`5a1a64a`；旧配置/锁未重签。正在执行的原S1目录保持原源码，补采在独立目录运行。
- 进度可接续：每次模型/任务尝试先保存claim，已有结果只读复用；中断时保留已知/未知用量，不重复发起同一已登记尝试；恢复控制器可等待仍活跃的worker，或只处理已有trajectory。不同模型独立进程、状态、历史、输出目录和会话ID。
- 测试命令：`.venv/bin/python -m pytest -q tests/test_teacher_followup.py tests/test_teacher_collection.py tests/test_teacher_data.py`：64 passed。
- 受影响旧S1语义测试使用临时测试源码锁，不改生产锁；历史生产锁仍拒绝修改后的源码。`.venv/bin/python -m pytest -q`：1056 passed，1 skipped；`.venv/bin/ruff check src scripts tests`与`format --check`通过，219文件。

上述测试使用合成环境，不代表45题已完成或GPT更强。首个启动因私有凭据文件仅含key、未包含基础网址而在请求前停止；0模型请求，两个worker启动记录保留在`followup_001`。启动环境显式沿用原网址后，新run `followup_002`完成首题真实DeepSeek和GPT尝试：两次均声明完成但未通过官方验收，0基础设施异常；累计20次请求、184,874输入/5,067生成token、201.08秒，配置费率估价USD0.029665。官方失败未导出为合格示范。随后两个独立worker继续剩余44题。

## 真实阻塞与独立格式约定（11:05，UTC+8）

初版完成7次真实尝试：4次DeepSeek、3次GPT，0成功/0合格，0服务异常；75请求、838,242输入/25,444生成token，累计episode792.80秒，配置费率估价USD0.097692。3个任务已完成两个模型尝试，另1题只完成DeepSeek。新题调度已暂停并等待活跃worker结束，原记录通过归档与744文件SHA回传核验。

最早两个金额问答中，两模型都把金额写成带`$`的字符串（其中一题还含千分位逗号），官方数值解析因此失败。此事实说明换模型不能自动解决公开输出契约缺口；没有据此判定数值本身正确，也没有剥离符号后重新评分。

增加独立`public_numeric_answer_format_v1`：作为公开任务JSON的`answer_format_contract`字段，要求单一金额/计数答案采用JSON数字，并明确保留名称、标识符、多值、百分比和用户指定格式。原instruction、execution_v6系统提示、Schema、Harness、评分及答案均不改；不识别task ID、不访问评分器、不替模型转换数值。DeepSeek/GPT共享同一条公开规则，导出时验证版本和实际可见规则；该字段位于用户上下文，loss全部屏蔽。

新配置/锁`teacher_followup_format_20261114`、新源码目录与run只读承接旧7次尝试，逐项核对config/source、trajectory及evaluation SHA；不重发同一模型/任务slot，不覆盖初版结果。原45题上限不变，已完成的3题不追加第三次尝试；继续余下42题的未执行模型slot，最多再83次。旧、新提示分别留痕，不能将补采成功率变化解释成纯模型切换效果。

新增及相关测试66 passed；完整1058 passed/1 skipped；Ruff221文件通过。真实新run已启动，源码锁SHA `f701ba9afc2af5d56aac7c2dbb4d1238bf9cfcd137e4c8f1596735d255801d68`。原Student dev24使用原提示，最终6/24；未重评或修改旧分数。

## 私有位置与启动

当前远端根：`/root/gpufree-data/recoveragent_teacher_followup_20261114_02/`、输出`runs/followup_003/`；本地运行资料：`runs/teacher_followup_20261114_02/`。初版目录`*_01`、run001/002及其源码/锁/归档保留。复用已验证的CPU AppWorld/tokenizer环境，不启动新的GPU模型或训练。原S1 dev24已独立完成6/24，未使用这次新增提示或补采数据。

在独立源码目录、原CPU环境中（`deployment.json`保存已授权机器的数据根、凭据路径及原23:47:24绝对截止时间）：

```bash
export PYTHONPATH="$PWD/src:$PWD"
export HF_HOME=/root/gpufree-data/recoveragent_base64_20261112/hf_cache
export HF_HUB_OFFLINE=1
export DEEPSEEK_BASE_URL=https://opencode.ai/inference/go/openai/v1
PYTHON=/root/gpufree-data/recoveragent_s1_20261113_01/worker_env/bin/python
# 新run准备一次；已有run只执行collect接续，不重做prepare。
"$PYTHON" scripts/collect_teacher_followup.py prepare --output runs/followup_003 --deployment deployment.json
"$PYTHON" scripts/collect_teacher_followup.py collect --output runs/followup_003
```

完成后导出独立`export/successes.jsonl`与manifest，报告DeepSeek/GPT各自尝试、官方成功、过滤/暂缓和用量。远端独立守护负责控制器异常退出后的接续和结束归档，本地只读观察器负责SSH重连、归档回传及逐文件SHA校验；不重复已登记的付费尝试。当前尚无补采完整统计，不声称模型效果提升。

45题难度为Easy8、Medium16、Hard21，是此前恢复未成功的子集，不能当作随机Teacher能力测评。已核对前10次完成尝试的初态SHA，全部与对应Student原始初态相同；没有把旧失败状态带入从头尝试。新数据仍按官方验收和完整编码准入，不为提高计数改分、追加同题尝试或放宽过滤。
