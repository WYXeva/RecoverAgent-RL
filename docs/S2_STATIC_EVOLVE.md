# 当前有限正确性验收（2026-11-17）

本轮4条历史Judge、一个生成context和一道新题×4次S0推理已完成。2题Solver通过，Student人工核对2/4；原Judge错误报4/4，定向提示修正后仍有理由算术错误。0参数更新，远端服务退出、证据已备份。正式Static/Evolve继续暂停。独立 `v2_s2_teacher_context_20261117` 使用Go的1,000,000窗口与匹配DeepSeek tokenizer估算；Student仍64k。具体命令、版本绑定与未验证内容见[本轮报告](../reports/s2_teacher_context_validation_20261117.md)。下面完整实验说明保留为前次准备记录，不属于本轮执行范围。

# S2：轻量 CoEvolve / LLM Judge 运行说明

以下为 `cc482d6` 时的本地准备记录；当时没有调用Solver/Judge。该历史锁不会接受本轮源码，正式重启前还需使用一致的新版本共同任务池及预算，不可直接运行旧命令。新路线为四次真实反馈→必要探索→三字段出题→一次Solver→共享Judge→入池→GRPO。

## 当前配置与边界

- `RECOVERAGENT_S2_EXECUTION=v2_s2_light_llm_20261117`，独立224文件源码锁；显式奖励身份 `coevolve_llm_reward_v1`，完成提交仍为 `completion_submission_v2`。
- 基础配置沿用 `v2_s2_static_evolve_20261116.json` 的模型、S0、train100、dev24/test111、64k/vLLM、5×40×4及GRPO设置；执行配置覆盖轻量生成和有限Judge预算。不得混用旧运行state、actor或评价缓存。
- 四份完整有效反馈直接用于出题；有成功的业务观察时直接抽象，否则至多一次针对性探索。每context最多2题，允许空列表、单动作/计数组件题；只做同源公开题面去重。保留真实反馈与原件，超窗明确暂缓，不剪切历史或追加旧六字段分析链。
- 每候选至多一次干净初态Solver和一次LLM Judge；旧证据适用时只做Judge。新任务不需要数据库目标表达式、字段元数据、repair或正负重放。不会回落到旧hash或全文答案比较。初态SHA仅核对执行起点，不是业务成功标准。
- Solver和Student的生成题由同一Judge判断；允许合法不同做法，正常未完成为0，Judge/环境故障无效。原始train、dev和test仍官方评分。两分支都需要生成题Judge服务。
- Teacher后台出题，Student不等候出题；组边界入池、每轮限频、跨轮复用、原始补位和无效整组排除沿用原实现。

四份生效主提示位于 `src/recoveragent/coevolve_prompts.py`：`EXPLORATION`、`GENERATION`、`SOLVER`、`JUDGE`。Actor基础工具协议与共享公开数字规则继续复用。旧第三方模板仅保留来源，不进入新生成/Judge文本请求。

## 已完成的私有准备

`runs/s2_light_llm_20261117/` 保存本轮准备；不会提交到Git：

- `initial_candidates/`：原9题全部找到适用Solver证据，逐份核对原引用SHA、公开题目、来源与初态；100份原反馈保留。9题只是待Judge候选，尚未获得新协议准入。
- `judge_check_plan.json`：历史48条中的第5/21条已确认成功、第7/18条真实失败，保留实际业务调用/返回和最终提交。人工标签只用于报告核对，绝不送入Judge。0新任务执行。
- `fresh16_plan.json`、`fresh16_READY/`：既定S0，单值数量、购物车JSON、商品提问、报价邮件各4次。3类生成题用新Judge，原始数量题官方评分。诊断定义不冒充已准入训练题，不自动入训练池。

仅离线重新准备时：

```bash
export RECOVERAGENT_S2_EXECUTION=v2_s2_light_llm_20261117
export PYTHONPATH="$PWD/src:$PWD"
.venv-v2/bin/python scripts/run_v2_s2.py prepare-pool \
  --input runs/v2_s2_20261116_prepare/initial_bundle \
  --references runs/s2_light_llm_20261117/initial_references_archive.json \
  --output runs/s2_light_llm_20261117/initial_candidates
.venv-v2/bin/python scripts/probe_s2_reward_chain.py prepare \
  --plan runs/s2_light_llm_20261117/fresh16_plan.json \
  --output runs/s2_light_llm_20261117/fresh16_READY
```

原始引用映射由本地已有归档提取，私有准备目录需整体迁移。不得把缺失证据伪造成通过；没有适用Solver证据时保留 `deferred_validation`，不自动重跑Solver。

## 后续获准后的核对和 fresh16

在新源码目录部署本次完整 `src/scripts/configs/requirements/third_party`；保留旧冻结目录。复制现有私有环境配置，不输出或提交密钥。以下使用现有worker环境；实际路径、S0/model snapshot/环境和可用GPU需届时核对。复制旧deployment为新文件，`initial_bundle` 指向后续共同新池，`deadline_epoch` 填新的已授权绝对UTC epoch；不沿用过期截止。

```bash
export RECOVERAGENT_S2_EXECUTION=v2_s2_light_llm_20261117
export PYTHONPATH="$PWD/src:$PWD"
export HF_HOME=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/hf_cache
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
S2_PY=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python
S2_DATA="$PWD/runs/s2_light_llm_20261117"
S2_DEPLOY="$S2_DATA/deployment.json"
S2_RUN="$S2_DATA/static_evolve_001"

"$S2_PY" scripts/run_v2_s2.py check-judge \
  --input "$S2_DATA/judge_check_plan.json" \
  --deployment "$S2_DEPLOY" --output "$S2_DATA/judge_check"

"$S2_PY" scripts/probe_s2_reward_chain.py run \
  --deployment "$S2_DEPLOY" --output "$S2_DATA/fresh16_READY"
# 中断时复用已完成结果，不重新采样已派发slot：
"$S2_PY" scripts/probe_s2_reward_chain.py resume \
  --deployment "$S2_DEPLOY" --output "$S2_DATA/fresh16_READY"
```

先核对4条Judge结果，再执行fresh16。逐条保留原始模型输出、解析参数、实际调用、终态提交、评分输入/响应、奖励有效性和用量。Judge判断不等于客观正确性的证明，需要核对已知正负例；若核对不符，记录具体原因后修复，不自动多轮复审或硬改成功。fresh16无训练，不要求16条全成功。

## 共同初始池、正式启动、续跑和评价

fresh16通过后，对已准备旧9题做一次新Judge准入，输出同一份共同池；不新增Solver执行。把新deployment的 `initial_bundle` 指向 `"$S2_DATA/initial_pool"` 的展开后绝对路径。接纳数可以小于9；Static/Evolve使用相同结果，空缺槽用原始题补齐。

```bash
"$S2_PY" scripts/run_v2_s2.py admit-pool \
  --input "$S2_DATA/initial_candidates" \
  --deployment "$S2_DEPLOY" --output "$S2_DATA/initial_pool"

# 终端/作业1：唯一Student控制器，先Static再Evolve，最终统一评价。
"$S2_PY" scripts/run_v2_s2.py run --mode both \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"

# 终端/作业2：同一环境变量、部署和output，CPU/API后台等待Evolve反馈。
"$S2_PY" scripts/run_v2_s2.py feedback-worker \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"
```

用已有持久终端或作业管理器分别保存两个进程日志，不同时启动两个Student控制器。同一个GPU串行执行两分支；后台worker可提前启动等待队列。新run从S0建立同协议dev24基线，每轮dev24，最终选择最佳轮（同分较早）与S0统一test111。旧S0历史分数保留，不导入新评价回执。

```bash
"$S2_PY" scripts/run_v2_s2.py resume --mode both \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"
"$S2_PY" scripts/run_v2_s2.py report --output "$S2_RUN"
"$S2_PY" scripts/run_v2_s2.py evaluate \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"
```

`evaluate`要求两分支五轮dev已完成，只接续最终评价。断点复用actor/optimizer/队列/反馈和已提交结果；Judge调用先持久化claim和预算，中断/结构错误不自动再次付费。正常业务失败仍为有效0；无效组不参与优势计算和能力失败反馈。

## 有限预算与成本记录

预算是技术上限，不是资源授权或账单承诺。各类请求另记实际input/output token、耗时、价格估算及中断预留；仍受新的实例绝对截止限制。

| 用途 | 调用上限 | 说明 |
| --- | ---: | --- |
| 历史Judge核对 | 4 | 2正2负，无新环境执行 |
| fresh16奖励Judge | 16 | 实际最多12条生成题，4条原题官方评分 |
| 共同初始池Judge | 9 | 复用全部9份Solver证据 |
| 正式Student生成题奖励Judge | 800 | 两分支总训练1600条，生成槽每分支至多50%，共享ledger |
| Evolve新增候选准入Judge | 192 | 最多24批×4context×2题，包含在生成预算中 |

正式Judge保守上限为 **1001次（800+192+9）**；初始池只有9题时Static最多180条生成轨迹、Evolve最多400条，实际理论上限进一步为 **781次**。未生成、未执行、故障或拒绝会使实际调用更少。历史核对4次和fresh16最多16次独立记账，不挤占正式额度。

生成总预算包含至多96次可选探索、192次Solver、96次出题、192次准入Judge：最多23,328请求、5,898,240生成token、380,160秒累计调用时间；这不是墙钟预计耗时。正式reward Judge为800请求/3,276,800生成token/96,000累计秒，另有有限input/priced_cost限制。每次文本最多4096生成token，原Actor单题预算保持80请求/16384生成token/1200秒。完整数值见 `configs/v2_s2_light_llm_20261117.json`；实际费用以provider账单为准。

---

# 历史运行说明（以下旧命令仅复现，不能用于当前新路线）

## 2026-11-16/17 历史版本

**当前停止状态**：本批远端续跑授权已撤回。11月17日的提交/奖励修复只在本地完成；下一步是固定16条协议诊断，入口、独立版本和等待条件见 [修复报告](../reports/s2_reward_chain_fix_20261117.md)。下文旧版本命令用于复现，不能据此自动恢复训练。新协议不能直接复用旧奖励或旧S0分数作为效果对照。

入口是 `scripts/run_v2_s2.py`，固定读取 `configs/v2_s2_static_evolve_20261116.json` 及独立源码锁。两分支从同一验收S0各自开始，只做GRPO；Recovery-SFT不在本阶段运行。训练采样、各轮dev24与最终test111使用当前GPU、固定vLLM版本、64k、公开数字格式和既定单题预算。S0 dev24完整复用20261115同协议结果；旧基线同型号L40S但物理GPU不同，该限制在报告中披露。

软件与数据准备见[验收报告](../reports/s2_static_evolve_implementation_20261116.md)。随后用户明确授权本次从启动起24小时，已于2026-11-16 13:31:44（UTC+8）启动，截止11月17日13:31:44；当前真实进度见[运行记录](../reports/s2_static_evolve_run_20261116.md)。仅使用既有L40S，不租机或充值。示例deadline仍为null，续跑使用私有deployment中同一绝对截止时间，不自动重新计时，也不重启已停止的Teacher补采。

## 固定实验

- 每模式5轮×40组×4次，共800个训练执行slot；每两组一次更新机会，LR `1e-6`，KL `0.001`。相同任务/策略/初态、不同种子；保留真实生成token和行为logprob。每次更新后换新actor，optimizer续接，KL reference永远为验收S0。恒定奖励和不可评分组不参与更新，零更新轮仍继续。
- 每轮原始槽与生成优先槽交替。原始100题连续打乱队列覆盖后重洗；生成题优先从未使用者，其次本轮未用者，每题每轮最多一组，跨轮可复用。没有生成题则原始补位。Static每轮9个生成组/31个原始组，不强凑50%。
- Evolve每8组处理刚完成窗口，取最新完整同题组。boundary/forgetting/all_failed/rare按冻结优先级排序，并保留一个all_failed名额，最多4个context。遗忘引用同任务此前最近8个完整组中的成功，不使用本组内先后关系。dev/test从不进入反馈。
- 每context两次独立干净探索、合并证据、抽象最多2题。只有合并输入实测超窗才分开各抽象最多1题；其他失败不触发此回退。保留源码模板和六个context字段，context全量超窗才进行可追溯摘要。原始消息、动作引用与验收记录保留。
- 复用新颖性、独立Solver、semantic judge、正负程序验收。当前 `v2_s2_admission_20261117` 接受简化子任务并取消反馈贴合硬门槛；合格新题在组边界加入后续未执行槽；第五轮最后不出题，总计至多24批/96context/192候选。已有9题不占新增额度。

每分支训练预算为64,000模型请求、13,107,200生成token。评价单独规划597个slot：S0 dev24一次、两分支各5次dev24、S0/两分支最佳轮各test111一次，最多9,781,248生成token。其中S0 dev24的24项本次复用历史记录，新增评价计划为573项；旧基线cost字段为历史用量，不再计作本次新增消耗，原001重复开销单列。每slot仍为80请求、16,384生成token、单回复4096、1200秒，失败不重试挑结果；中断可能使实际完整轨迹少于规划slot，报告另列。

Teacher生成/验证保留40,000请求、16,000,000生成token、172,800秒调用累计时间等上限。2026-11-16用户要求“不必管账单，重新生产任务”，新执行版 `v2_s2_generation_20261116` 停用累计USD100技术估价拦截，但继续记录用量、未知预留和价格估算，不修改原实验配置。每次调用的有限授权、请求/token/时间上限及实例绝对截止不变；旧执行版仍保留原价格上限。估价不是实际账单。

## 准备私有数据与部署

以下为仓库根目录命令。公开Git只保存代码、配置、任务ID与SHA，原始任务/轨迹/权重始终在私有目录。

```bash
PYTHONPATH=src:. .venv/bin/python scripts/run_v2_s2.py prepare \
  --archive runs/v2_s1_train100_20261113_01/retrieval/final_1794626440.tar.gz \
  --output runs/v2_s2_20261116_prepare/initial_bundle
```

该步骤已在本地实际完成；仅从原归档校验导入9个已验收任务定义和train100的公开历史，不重新采集或更改任务。配置 `v2_s2_inputs_20261116.json` 固定整个归档SHA及109个成员SHA。将完整bundle目录复制到部署路径即可，加载时重建公开历史路径，不改历史消息。

在新目录部署**本次提交的完整源码**，包括 `src/`、`scripts/`、`configs/`、`requirements/`、`third_party/`；以新锁验证，不重签旧锁。复用现有推理/CPU环境与基座缓存；GRPO训练使用独立依赖环境，默认路径见 `configs/v2_s2_deployment.example.json`。不要把新源码覆盖旧冻结运行目录。部署代码根目录需要既有私有 `.env`，使用现有Teacher凭据；不要将其提交。Static不调用Teacher。

```bash
mkdir -p runs/s2_private
cp configs/v2_s2_deployment.example.json runs/s2_private/deployment.json
```

按实际新目录填写 `initial_bundle` 等路径，并在获得新的资源时限后将 `deadline_epoch` 填为该**绝对UTC epoch秒数**。程序拒绝null和过期截止，不读取历史配置中的相对小时数。当前部署：新 `training_env/bin/python` 通过只读`.pth`复用已验收SFT环境的Torch2.10/Transformers5.23开发版/PEFT，再独立安装 `requirements/v2-learning.txt` 中固定commit的TRL和datasets5.1.0；旧训练环境缺这两项，未被升级或改写。AppWorld与tokenizer使用既有 `worker_env/bin/python`，推理使用独立vLLM0.19环境。实际依赖冻结在私有run的 `training_environment.lock.txt`，不要直接给旧SFT环境升级包。

## 启动、续跑、评价

在新源码根目录执行；下列环境变量路径与示例部署一致。用持久终端/现有作业管理器保存控制器日志，不在同一个output并行启动两个控制器。

```bash
export PYTHONPATH="$PWD/src:$PWD"
export HF_HOME=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/hf_cache
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export RECOVERAGENT_S2_EXECUTION=v2_s2_generation_20261116
S2_PY=/root/gpufree-data/recoveragent_s1_retrain_20261114_01/worker_env/bin/python
S2_RUN=/root/gpufree-data/recoveragent_s2_20261116_01/runs/static_evolve_002
S2_DEPLOY="$PWD/runs/s2_private/deployment.json"

"$S2_PY" scripts/run_v2_s2.py run --mode evolve \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"
```

`both`先读取S0 dev24基线缓存，随后static、evolve各自五轮，最后自动选最佳轮并完成三份test111。当前002已按5118份SHA证据导入整套旧基线，包含原1项未知，不再生成S0 dev24；`baseline_reuse.json`保留来源及跨物理GPU说明。原001误重跑的记录另存、不参与主比较。空白新output若无经核验缓存才会执行S0基线，勿误用新output重复评价。也可用相同output先 `--mode static`，之后 `--mode evolve`；复用已完成基线，两个分支不共享actor或optimizer。不在同一GPU并发两分支。

```bash
"$S2_PY" scripts/run_v2_s2.py resume --mode evolve \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"

"$S2_PY" scripts/run_v2_s2.py report --output "$S2_RUN"

"$S2_PY" scripts/run_v2_s2.py evaluate \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"
```

`resume`恢复当前轮、原队列、池、近期反馈、RNG、窗口、actor/optimizer；完整receipt复用，已派发且无完整结果的slot保留unknown，不重发。未提交更新从保存parent重做，不重复提交已完成更新。Teacher中断context保留已接纳题与未知成本，不重复付费探索。`evaluate`只接续最终test，要求两个分支均已完成五轮dev，不绕过主流程选模型；已经执行过的评价不重试。

用户明确要求重做零候选失败窗口时，先在保存边界停止旧调度器，再使用独立的新尝试ID：

```bash
"$S2_PY" scripts/run_v2_s2.py regenerate --window 8 --attempt user_001 \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"
"$S2_PY" scripts/run_v2_s2.py resume --mode evolve \
  --deployment "$S2_DEPLOY" --output "$S2_RUN"
```

重做复用原窗口选出的四份组级反馈，不使用后续轨迹；新输出位于 `generation/window_008_retry_user_001/`。原 `window_008`、评分、actor/optimizer和队列保留；新合格题立即进入后续槽。原窗口已有候选时拒绝重复出题；同一尝试ID可恢复已保存结果，不重复发请求。报告分别统计常规批次与显式重做批次。`regenerate`与训练调度共用控制器锁，不允许两个进程并发修改任务池。

新执行版修复真实Teacher入口未接受S2 runtime、预算文件抢先创建Teacher输出目录、摘要分片未逐条给出全局编号三个阻塞。预留文件改为相邻 `.budget/`，仍从真实轨迹核销；摘要校验继续严格拒绝无效引用。186文件新锁覆盖全部修改依赖，历史锁未重签。该软件修复不等同于真实候选或验收成功，实际结果另记。

当前按用户21:13的指示优先 `--mode evolve`，不是重新运行Static。远端监督器为 `controller_evolve.py`，输出仍为 `static_evolve_002`；Static停在20组/6次更新，Evolve从共同S0独立开始。后续恢复Static用同一output的 `resume --mode static`，不要另建空白目录重跑基线。原绝对截止11月17日13:31:44不变，分支切换不重新获得24小时。两分支未全部完成五轮时不启动最终test。

并发执行版 `v2_s2_parallel_20261116` 使用独立执行配置和184文件源码锁，保留原实验配置、协议版本、历史锁和state binding。原锁在新源码上仍拒绝；必须显式设置上面的执行版本，子进程继承并核验同一新锁。每组最多2个独立AppWorld进程共享已预热的vLLM服务，线程分别持有HTTP session，按member序号收齐4条后才允许后续更新。Static/Evolve使用相同并发；dev/test仍串行。启动保存 `executions/v2_s2_parallel_20261116.json`，每组记录并发和本次调度墙钟时间。执行版本切换不重置actor、optimizer、任务队列或资源截止；在完整组边界停止旧控制器，再用新源码及原output续跑，不能同时运行两个控制器。

首次vLLM启动运行既有加载/短请求/缓存/双64k探针，后续actor重新绑定并核对权重。训练worker独立加载当前actor和固定S0 reference，检查有限梯度、参数变化和保存重载后才能发布新actor。真实64k GRPO的显存、吞吐与收益尚未验证；OOM/非有限梯度/重载失败停止并留日志，不能把CPU微型测试当显存验收。

## 输出、磁盘与验收

主目录保留 `experiment.json`、`status.json`、基线、两分支 `state.json`、`groups/`、`updates/`、`generation/window_*/`、轮末 `evaluation/`、`round_*_report.json`、`summary.json/.md`；最终选择见 `final_selection.json`，test结果及相对S0新增成功/退步见 `test_summary.json`。实际首轮输入/工具/token/采样和初态配对审计见各评价目录 `paired_inputs.json`。

每窗口保留选题原因、四条结果、context/探索/候选/接纳数及拒绝原因、Teacher用量；每轮保留原始/初始生成/新增三类组奖励、异常、有效更新、非零优势且进入更新的生成题、Student/Teacher token与耗时、约5个来源链案例。五轮dev曲线、末轮和最佳轮见summary；同分选较早轮，训练不回滚到最佳轮。实际账单不可获得时明确null。

现有数据盘只剩约14GiB，故新S2采用有限保留：保留每轮actor+optimizer和最新可续跑状态，已提交且被替代的中间参数文件可删除，但SHA、更新/重载证明及 `retention.json` 保留；被退休的中间actor不再宣称可加载。历史S0、旧训练和旧数据不参与清理。

提交更新或评价完成后，重复的请求JSON压缩为 `session/requests.tar.gz`，逐成员SHA验证后才移除散文件；token/logprob原件完整保留，未提交更新前不清理。审计时可将压缩包解到独立目录。公开交互历史单独保留供反馈使用。仍需监测实际盘占用；压缩与有限checkpoint保留不保证1600长轨迹一定可容纳，不自动删历史产物来续跑。


## Teacher后台生成与无人值守接续（2026-11-17）

用户要求减少等待后，新执行版为 `v2_s2_background_20261117`。Student每8组保存不可变反馈作业后立即继续；单个CPU/API Teacher worker顺序处理队列，复用原四轨迹context、探索和验收。仅Student控制器写学习状态，新合格任务在完整任务组边界导入一次。生成滞后与未实际入训数量如实记录，不将“已入队”算成已生成。运行中的旧Teacher允许在原state目录完成，新Student接续使用独立state目录，模型、更新和生成日志仍可共享有身份核验的原产物。

在新源码目录使用原实例绝对deadline的deployment启动；断开SSH后监督器仍运行。同一命令也用于接续，已有轨迹/更新不重复消费：

```bash
export RECOVERAGENT_S2_EXECUTION=v2_s2_background_20261117
export PYTHONPATH="$PWD/src:$PWD"
nohup "$S2_PY" -u scripts/supervise_s2_background.py \
  --deployment "$S2_DEPLOYMENT" --output "$S2_OUTPUT" \
  > "$S2_OUTPUT/supervision-launch.log" 2>&1 < /dev/null &
```

监督器同时管理 `run_v2_s2.py resume --mode evolve` 与 `run_v2_s2.py feedback-worker`；只有一个Teacher预算写入者。当前源拒绝在后台执行版下额外启动同步 `regenerate`，避免双重消费；已有原版本重跑通过只读产物移交接续，不中断付费请求。

用户允许跳过OOM：推理异常保留unknown和实际请求预留，在组边界重启后端；训练明确CUDA OOM保存跳过回执，丢弃该工作进程未验收更新，沿用之前的有效actor/optimizer，继续后续组。其他非有限梯度、来源/哈希或重载错误不冒充OOM。监督器每10秒写状态，对可恢复进程错误至多连续自动接续3次；有Student进展后重置连续失败计数。原始日志、恢复次数和停机原因见 `supervision/`，汇总由已有report函数生成。资源截止后保存进度并停作业，不自动租机、续费或重置时限。


## 简化子任务与多值答案准入（2026-11-17）

用户最新指定执行版 `v2_s2_admission_20261117`，保留后台队列和OOM处理。较少对象、条件、操作或仅练一个组件都可以成为新任务；取消 `feedback_fit` Teacher调用和拒绝门槛。完全相同的任务和纯措辞/格式变化仍不是新的实质任务。

多值提案可给出 `answer_fields`（字段名→number/integer/string/boolean）。构造新任务时将其渲染为公开JSON答案说明；购物车基价、税额、总价这一明确三值请求也有公开文本规则支持。三方使用同一比较契约，不在执行后删单位、猜答案字段或改写模型输出；缺字段、多字段、错类型和错值仍失败。历史无此声明的任务保留原比较方式。原始提案与渲染后的提案分别保存，新任务标注 `coevolve_curriculum_quality_v2`。

当前后台监督器使用同一启动命令，只把执行变量设置为：

```bash
export RECOVERAGENT_S2_EXECUTION=v2_s2_admission_20261117
```

按用户要求重新生成一次时，在Student完整组边界、控制器锁释放后，先将原窗口的冻结四轨迹选择入队，然后启动/接续监督器：

```bash
"$S2_PY" scripts/run_v2_s2.py regenerate --window 8 --attempt admission_v2_001 \
  --deployment "$S2_DEPLOYMENT" --output "$S2_OUTPUT"
```

新输出为 `generation/window_008_retry_admission_v2_001/`，旧候选/成功/拒绝记录不修改；同尝试ID不重复入队。该次重做有独立用户授权，计入额外生成成本，不伪装成原首批首次结果。


## 逐批误判复核与自主接续

当前新源码选择 `RECOVERAGENT_S2_EXECUTION=v2_s2_supervised_20261117`，使用同名配置和新锁。上面的监督器启动命令保持不变；只在旧监督器已经退出时启动，不并行写同一个state。当前远端输出仍为 `runs/static_evolve_003`，S0基线与历史轨迹继续复用。

每个完成context保存 `generated_task_review_v1/context_result.json`，通过 `published/review_context_N.json` 发布。语义拒绝复用原Solver，重复拒绝复用quality与完整Solver验收；真实错误/重复和证据不足不放行。原判及复核分列，`generation_reviews` 与summary可追溯更正题及后续入训。复核请求计入原Teacher有限request/token/time预算，不增加候选数。

私有deployment可记录一次受控移交的 `adopted_update`（PID、创建时间、原payload路径及SHA、groups_through）。监督器验证这些值，保留正在计算的原进程；原结果通过后才由已有resume入口提交，Teacher可同时继续。该字段必须来自真实进程与原请求，不能用来改写预期值或放行不一致。常规续跑不需要手工填写该字段。

配置 `finish_static_after_evolve=true` 在Evolve五轮评价完成后接续原Static，并走原最佳轮/test流程；不会因转分支重置资源截止。`supervision/status.json` 记录实际分支、进程、更新及错误，原始结果和停机原因保留。

监督器的受控移交还可记录 `inherited_teacher`：真实PID、创建时间和原源码目录。新监督器核验同一feedback-worker与output后继续监管原进程，不中断正在执行的Solver或重复启动第二个Teacher；新启动的子进程使用新锁。原GPU更新退出的过渡窗口延后检查其保存结果，不将空argv误判为换了进程。
