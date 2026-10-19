# 环境验收记录 · 2026-10-19

需求基线 v0.2。只执行 P0–P2，无 Teacher、SFT、GRPO、训练器安装或参数更新。

| 项目 | 实际检查结果 |
| --- | --- |
| 工作目录 | `/home/eva/project/RecoverAgent-RL`；原始 `.git` 为空、只读 |
| Git 开发副本 | `/tmp/recoveragent-dev-20261019`，从远端历史克隆 |
| Remote / 分支 | `https://github.com/WYXeva/RecoverAgent-RL.git` / `codex/p0-p2-recovery` |
| 起始提交 | `14be212003e2af85457377eaf4e154208ea978c7` |
| Python | 3.14.4；两个独立 venv |
| CPU / RAM | Intel i5-14400F，16 逻辑 CPU；约 15GiB RAM，4GiB swap |
| GPU | NVIDIA RTX 5050，8151MiB；驱动 610.62 |
| AppWorld | `0.2.0.dev0`，commit `42b5bcf3cd334fee33f0c37c02070a9f5807add5` |
| 基准数据 | 官方下载，`data/version.txt` 为 `0.2.0`；只执行 train |
| Student | `Qwen/Qwen3-1.7B`，revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e` |
| 推理栈 | PyTorch `2.10.0+cu128`，CUDA runtime 12.8，Transformers 4.57.6 |
| 推理模式 | 本地 BF16，`enable_thinking=false`，单请求；无付费服务 |
| Tokenizer | `Qwen2TokenizerFast`，与模型同 revision；工具 XML 标签经实际编解码验证保留 |
| Chat template SHA-256 | `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8` |
| Teacher / TRL / PEFT | 未配置 / 未安装 / 未安装；P3 未开始 |

CUDA 已执行实际小张量运算，`2 × 2` 返回 `4.0`；不能仅据 `nvidia-smi` 宣称模型或训练可用。模型加载后设备总显存约 5071MiB；长上下文恢复期间一次采样读数为 7855MiB / 8151MiB（均含桌面等其他进程）。这是观察到的设备读数，不是完整峰值追踪或训练显存估计。

机器可读版本证据为 `environment_appworld.json`、`environment_inference.json`；完整依赖为 `requirements/appworld.lock.txt`、`requirements/inference.lock.txt`。两个权重分片均已对照 Hugging Face 对象 SHA-256 验证，大小和哈希见 `student_weights.json`，权重本体不提交。

## 实际问题及处理

1. 普通沙箱中的 Git/pip DNS 和 GPU NVML 访问失败；经自动审批运行已授权的联网安装、资源检查和进程通信后可用。没有把初次沙箱失败误报为本机无 GPU。
2. 最新 Pydantic 2.14.1 与 AppWorld 补丁不兼容；固定 2.12.3，导入和实际 world 均通过，`pip check` 无依赖冲突。
3. 源码 ZIP 的 bundle 为 Git LFS 指针；下载同 commit 对象并核验哈希后使用官方 CLI 解包。缓存设在可写的项目 `.cache/appworld`。
4. `load_state()` 关闭 world 时钟后未重建旧 freezer；适配层做版本明确的时钟重建。保留失败的真实日志，未修改依赖源码。
5. 官方评价生成 `__pycache__` 导致初版基础输入指纹误报；仅在基础输入版本检查中排除派生字节码，原始文件仍全量校验。
6. Student 首次下载因连接中断失败；一次有 20 分钟上限的断点续传完成，未切换不明来源模型。

## 真实状态测试

实际命令（在项目根目录，相关环境变量指向项目可写路径）：

```bash
APPWORLD_CACHE="$PWD/.cache/appworld" IPYTHONDIR="$PWD/.cache/ipython" PYTHONPATH=src \
  timeout 600 .venv/bin/python scripts/state_probe.py --output runs/p0_state_20261019_d
```

结果 `PASS`，原始日志 `.cache/state-probe-d.log`。通过公开 API 修改真实 supervisor Task 表；保存修改后的非初始状态，进一步修改后恢复；随后四个不同 PID 的 worker 导入同一快照，各自独立写入不同内容并再次恢复。四次原始任务官方评价均实际运行。摘要 `p0_state_evidence.json` 含逻辑状态、历史哈希、独立 PID 和结果。

该探针是人为编排的基础设施测试，不作为自然失败、不进入训练 Buffer。P0 的 Student 执行、P1 自然失败及 P2 恢复以独立模型实验报告为准。

## 证据路径

- `.cache/install-appworld-approved.log`、`.cache/install-compat.log`、`.cache/appworld-unpack-v5.log`、`.cache/appworld-download-v3.log`：实际安装与数据日志。
- `.cache/state-probe-b.log`、`.cache/state-probe-c.log`：兼容性错误；修复后 `d` 完整通过。
- `.cache/install-torch.log`、`.cache/install-transformers.log`、`.cache/download-student.log`、`.cache/download-student-retry1.log`：推理环境及下载记录。
- `.cache/student-server.log`：模型实际加载与服务身份。
- `software_tests.txt`、`static_checks.txt`：软件验证；与真实环境验收分开。

上述 `.cache/`、`runs/` 原始证据仅保留本地；公开仓库只包含允许公开的摘要、配置和代码。
