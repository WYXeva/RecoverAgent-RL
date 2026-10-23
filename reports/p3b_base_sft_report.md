# P3-B Qwen3-4B Base-SFT执行报告

2026-10-23进行中。用户已明确授权真实训练及结束关机。此版本仅记录已完成的前置检查，不能视作训练通过。

- 原数据95条轨迹/75任务/29家族；train82轨迹/64任务/24家族/925Assistant样本/229776监督token；内部validation13/11/5/147/43227。最长完整样本train31102、validation32610，32768窗口内，不截断。
- 可信manifest `5b297cf38d7b23cb6dc02c461a0bb030c69e58d938bffda9c7569a67ad44b24c`；归档 `68231f625b876db259c5085d08e70c77604ffbaaf6b0809b807852e2bfc3b9ed`。本地/远程18源包复验通过，重新分词逐字节一致；原95条与原哈希不改。
- 训练封存源码473693c3b768940679a2367d149fd54293e27496，50文件锁匹配；当前worker从2145a930加独立推理/评估入口，不修改P0–P2或旧锁。
- 实测RTX PRO6000 Blackwell Server Edition、97887MiB、驱动595.71.05、Python3.12.3、torch2.10.0+cu128、CUDA12.8。BF16真实矩阵计算和全部固定依赖版本预检通过。容器22CPU/110GiB内存；实际云账单单价未知。
- 远程AppWorld官方安装、真实写入/命名保存恢复/四进程隔离脚本探针通过；这不是Teacher或Student成绩。
- 新增真实Qwen3批量生成后端、固定6任务Raw/Base-SFT对照入口和短/长/正式顺序门槛控制器。代码测试与训练验收分开。
- 短烟测、最长样本烟测、正式optimizer steps、checkpoint重载和真实模型评估：**待执行，无结果**。

当前机器证据：`p3b_local_*_20261023.json`、`p3b_remote_*_20261023.json`。原归档导入函数字段network_transfer_performed=false表示该函数本身不实现网络；本轮已另外通过授权SCP完成网络传输，并在远程核验同一可信归档SHA。

网络诊断：远程HF直连失败、本机Xet无进展；普通HTTPS与AutoDL学术加速可用但慢，官方ModelScope镜像用于传输，完整内容必须匹配独立HF固定revision哈希才可加载。所有失败下载日志保留于私有.cache/p3b_*及远程logs，不上传凭据/权重/原始数据。
