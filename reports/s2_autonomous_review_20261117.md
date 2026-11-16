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

部署及最新任务入池回执在完成实际切换后追加。本轮完整S2仍在进行，不把代码完成称为五轮完成。
