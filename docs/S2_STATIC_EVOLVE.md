# S2：Static / Evolve 五轮运行说明

入口是 `scripts/run_v2_s2.py`，固定读取 `configs/v2_s2_static_evolve_20261116.json` 及独立源码锁。两分支从同一验收S0各自开始，只做GRPO；Recovery-SFT不在本阶段运行。训练采样、各轮dev24与最终test111使用当前GPU、固定vLLM版本、64k、公开数字格式和既定单题预算。S0 dev24完整复用20261115同协议结果；旧基线同型号L40S但物理GPU不同，该限制在报告中披露。

软件与数据准备见[验收报告](../reports/s2_static_evolve_implementation_20261116.md)。随后用户明确授权本次从启动起24小时，已于2026-11-16 13:31:44（UTC+8）启动，截止11月17日13:31:44；当前真实进度见[运行记录](../reports/s2_static_evolve_run_20261116.md)。仅使用既有L40S，不租机或充值。示例deadline仍为null，续跑使用私有deployment中同一绝对截止时间，不自动重新计时，也不重启已停止的Teacher补采。

## 固定实验

- 每模式5轮×40组×4次，共800个训练执行slot；每两组一次更新机会，LR `1e-6`，KL `0.001`。相同任务/策略/初态、不同种子；保留真实生成token和行为logprob。每次更新后换新actor，optimizer续接，KL reference永远为验收S0。恒定奖励和不可评分组不参与更新，零更新轮仍继续。
- 每轮原始槽与生成优先槽交替。原始100题连续打乱队列覆盖后重洗；生成题优先从未使用者，其次本轮未用者，每题每轮最多一组，跨轮可复用。没有生成题则原始补位。Static每轮9个生成组/31个原始组，不强凑50%。
- Evolve每8组处理刚完成窗口，取最新完整同题组。boundary/forgetting/all_failed/rare按冻结优先级排序，并保留一个all_failed名额，最多4个context。遗忘引用同任务此前最近8个完整组中的成功，不使用本组内先后关系。dev/test从不进入反馈。
- 每context两次独立干净探索、合并证据、抽象最多2题。只有合并输入实测超窗才分开各抽象最多1题；其他失败不触发此回退。保留源码模板和六个context字段，context全量超窗才进行可追溯摘要。原始消息、动作引用与验收记录保留。
- 复用新颖性、独立Solver、semantic judge、正负程序验收，并核对反馈贴合。新题立即加入后续未执行槽；第五轮最后不出题，总计至多24批/96context/192候选。已有9题不占新增额度。

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
