# S2逐批误判复核与自主接续

2026-11-17。按用户持续授权，在当前S2上增加逐批复核与接续；原模型、实验配置、候选和评审记录保持不变。独立执行版 `v2_s2_review_20261117`，196文件新锁。

## 已完成软件工作

复用quality、Teacher、Solver及正负验收，新增每context补充复核：语义误判重用原公开轨迹，检查真实动作引用并提供逐次观察的计数/金额和；重复拒绝比较同源原题及已接纳池，允许真实简化。每候选最多一次付费复核，原记录不覆盖，已人工纠正者不重审。更正任务经原验收后发布独立回执，由Student在组边界导入一次。报告分列初判与复核，不把更正当新增候选。

Teacher全队列前驱检查保证一个预算写入者。监督器可保留并接管正在计算的原GPU更新，核对PID/请求SHA/组/配置，等待成功重载或明确OOM跳过后接续；不重启该更新。配置保持Evolve优先，完成后接续暂停的Static与原最终评价；绝对截止仍为2026-11-17 13:31:44（UTC+8）。

## 验证

- `.venv-v2/bin/python -m pytest -q tests/test_s2_review.py tests/test_s2_background.py tests/test_s2_evolution.py tests/test_coevolve_curriculum.py tests/test_coevolve.py tests/test_coevolve_quality.py`：67 passed。
- `.venv-v2/bin/python -m pytest -q tests/test_s2_generation_fix.py tests/test_s2_parallel.py`：9 passed；新锁通过、历史锁继续拒绝当前改动源码。
- `.venv-v2/bin/python -m pytest -q`：1188 passed，13条已有依赖弃用警告。
- `.venv/bin/ruff check src scripts tests`、`ruff format --check src scripts tests`：通过，260文件。

新增测试使用合成Teacher/轨迹，验证不伪造动作引用、不修改答案、不跳过正负验收、真重复仍拒绝、幂等入池后实际采样、全局预算互斥和活动更新接管。不是新真实任务成功率或学习增量验证。前两项人工纠正的真实AppWorld四模式重放见[既有生成报告](s2_curriculum_admission_20261117.md)。

## 真实执行

02:29:37（UTC+8）在原20组的完整边界完成真实接管，源码 `23b2e1657c2c124857f740da0c39feca30056f05`。新source包SHA `25f6ef271229a48cbf76d866977398e9265667f3b62fdc14f6a74949448af12d`，源码锁SHA `4457d0969742973b6b5ca717a9c5782452296fe834859b8c5d0d92b6fbcf0053`，远端196文件校验通过。

- 保留原GPU worker25993，使用原payload SHA `8efa5c2d49a4b811a1d882d4d27963b1fc8d267893c4494e37a44ada0cac7e00`；新监督器33375等待其结果，未启动第二个训练进程。02:30 GPU100%，原更新已进行约86分钟。当前仍4次已提交更新，不能提前计为5次。
- 在控制器锁下导入7条已验收题，池11→18：旧窗口16已完成的3条、当前新批自动通过2条、人工证据复核2条。后者ID `coevolve_bc9e198bc6cf12d4e12e0a84` 与 `coevolve_21e5c3f88c40053ac7ef28b0` 已在实际state；尚未被Student采样，不能算已参与更新。
- 原actor、optimizer、更新步数、20组记录与采样队列逐字段不变；Static state SHA仍为 `ceb90241b0c01b3ae4f4bdf3b5a69329dedb817cdba97aef236f983af59f5083`。Evolve80条为13成功/67失败/0未知。
- 新Teacher worker33376实际处于 `waiting_external`，原独立Teacher26958继续最后context；已支付操作未中断。全局互斥检查真实生效。每context自动复核已部署，但此时尚未发出新版本的首次复核模型请求；原两项纠正的真实验收已在此前完成。

私有输出仍为 `/root/gpufree-data/recoveragent_s2_20261116_01/runs/static_evolve_003/`；受控移交回执 `continuations/review_v1_001/switch_result.json`。最新state及来源证据回传本地 `runs/v2_s2_20261116_01/retrieval/review_20261117/` 并核验各文件SHA。新代码不覆盖旧产物/源码锁。

完整S2仍在进行，未获得新的轮末dev或最终test结果，不把部署完成称为五轮完成。
