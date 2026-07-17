# UACC 工程归档清单

盘点日期：2026-07-18

本清单面向 UACC 项目的源码上传、可复现实验归档和论文材料归档。路径均相对于仓库根目录 `/home/ling/gem5`。

当前实验侧目录结构为：

- `uacc/hw/`：HLS、OpenROAD、定点模型、消融实验和实验结果；
- `uacc/util/`：独立软件模型、对比工具和 SPEC/ChampSim 工具；
- `uacc/doc/`：模型、计划、硬件开销方案和本清单。

gem5 集成代码仍保留在原有的 `configs/`、`src/` 和 `tests/` 路径。旧的 `util.*` Python 路径仅保留兼容入口，不再承载实验实现。

## 一、必须归档：已提交的工程文件

这些文件属于 `uacc` 分支相对 `stable` 的工程提交，应随源码一起上传。

### 仿真模型、配置和设计文档

- `configs/example/uacc.py`
- `uacc/doc/model.md`
- `uacc/doc/plan.md`
- `uacc/doc/hardware_overhead_plan.md`
- `src/mem/packet.hh`
- `src/mem/serial_link.cc`
- `src/mem/serial_link.hh`
- `src/mem/serial_link_model.hh`

### UACC gem5 实现

- `src/mem/uacc/`（13 个文件）

### 测试

- `tests/pyunit/uacc/`（当前已提交的 3 个文件）

### 软件模型和 SPEC/ChampSim 工具

- `uacc/util/champsim_trace_to_packet.py`
- `uacc/util/compare_uacc_models.py`
- `uacc/util/run_uacc_spec_suite.py`
- `uacc/util/uacc_gg1_sim.py`

### 硬件评估源码、脚本和结果说明

- `uacc/doc/ARCHIVE_MANIFEST_2026-07-18.md`
- `uacc/hw/fixed_point_sweep.py`
- `uacc/hw/fixed_point_sweep_f1_f2_f4_10000.json`
- `uacc/hw/hls/`
- `uacc/hw/openroad/README.md`
- `uacc/hw/openroad/asap7/allocator/`
- `uacc/hw/openroad/asap7/collector/`
- `uacc/hw/openroad/asap7/cost/`
- `uacc/hw/openroad/asap7/full_results_2026-07-15.md`
- `uacc/hw/openroad/asap7/precision_sweep_results_2026-07-16.md`
- `uacc/hw/openroad/run_asap7_full.sh`
- `uacc/hw/openroad/run_asap7_smoke.sh`
- `uacc/hw/openroad/run_precision_sweep.sh`
- `uacc/hw/openroad/run_width_sweep.sh`
- `uacc/hw/range_precision_sweep.py`
- `uacc/hw/range_width_accuracy_q4_10000.json`
- `uacc/hw/range_width_sweep_q4_10000.json`
- `uacc/hw/range_width_sweep_results_2026-07-16.md`

## 二、必须处理：当前未跟踪文件

这些内容原先显示为 `??`，现已整理到 `uacc/hw/`；上传前应加入归档。如果通过 Git 提交，需要同时加入兼容入口和实验结果目录。

### 新增测试和分析脚本

- `tests/pyunit/uacc/pyunit_gg1_ucp_ablation.py`
- `uacc/hw/gg1_ucp_ablation.py`

### 三组 GG1/UCP 实验结果

以下三个目录各包含 `ablation_section.md`、`summary.json`、`sweep.csv`、`trace.csv`、PNG 和 PDF：

- `uacc/hw/gg1_ucp_ablation_results/`（6 个文件）
- `uacc/hw/gg1_ucp_contention_results/`（6 个文件）
- `uacc/hw/gg1_ucp_feedback_stress_results/`（6 个文件）

### Memory technology 结果

- `uacc/hw/memory_tech_results/memory_technology_parameter_table.csv`
- `uacc/hw/memory_tech_results/normalized_performance_proxy.png`

为保持原有 gem5 测试和旧命令兼容，以下入口保留在原路径：

- `util/uacc_gg1_sim.py`
- `util/compare_uacc_models.py`
- `util/run_uacc_spec_suite.py`
- `util/champsim_trace_to_packet.py`
- `util/uacc_hw/fixed_point_sweep.py`
- `util/uacc_hw/range_precision_sweep.py`
- `util/uacc_hw/gg1_ucp_ablation.py`

原始实验侧新增内容合计 22 个文件；整理过程中另增加了少量 Python 包初始化文件、兼容入口和旧临时目录的忽略文件。

## 三、建议保留：已整理的 OpenROAD ASAP7 结果

这些目录被 `uacc/hw/openroad/.gitignore` 忽略，但它们是已经整理好的归档结果，不是临时工作区。建议上传到归档包；通过 Git 提交时需要强制加入。

- `uacc/hw/openroad/asap7/precision_sweep_results/`：156 个文件，约 6.8 MB
- `uacc/hw/openroad/asap7/width_sweep_results/`：208 个文件，约 9.2 MB
- `uacc/hw/openroad/asap7/q8_16_archive/`：52 个文件，约 3.2 MB

三组结果共 416 个报告、日志、指标 JSON 和可视化图片，约 19.2 MB。

## 四、可选保留：论文/交付材料

- `uacc/doc/acm-sigconf.pdf`：论文/交付材料 PDF，已从仓库根目录归入文档目录。

## 五、明确排除：临时生成物

三个已整理的 ASAP7 结果目录已复制到 `uacc/hw/openroad/asap7/`。旧位置下的 precision/width 结果副本因目录归属为 `root:root`，当前用户无法删除；它们不属于新的归档目录，上传时只取 `uacc/hw/` 下的副本。

以下内容不建议进入上传归档：

- `util/uacc_hw/openroad/work/`（旧位置的临时工作区）
- `util/uacc_hw/openroad/work_full/`（旧位置的临时工作区）
- `util/uacc_hw/openroad/work_precision_f2/`（旧位置的临时工作区）
- `util/uacc_hw/openroad/work_precision_f4/`（旧位置的临时工作区）
- `util/uacc_hw/openroad/work_precision_sweep/`（旧位置的临时工作区）
- `util/uacc_hw/openroad/work_width_sweep/`（旧位置的临时工作区）

这些目录共 349 个文件、约 2.6 GB，主要是可由脚本重新生成的 OpenROAD 中间数据库和临时对象。还应排除：

- `util/uacc_hw/__pycache__/`（旧位置的缓存）
- 仓库级 `build/`、`m5out/`、各处 `__pycache__/`
- `src/arch/parser.out`
- `.humanize/`

## 六、归档规模

按当前目录估算：

- UACC 已提交源码、配置、文档和脚本：约 1.4 MB
- 当前未跟踪结果和脚本：约 0.5 MB
- 整理后的 OpenROAD ASAP7 结果：约 19.2 MB
- 推荐的源码加可复现结果归档：约 20 MB
- 若加入论文 PDF：约 21 MB
- 若加入 OpenROAD `work*` 原始工作区：约 2.6 GB，不建议默认加入

本清单本身不移动或删除任何文件，也不改变 Git 暂存区。
