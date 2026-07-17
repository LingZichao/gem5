# UACC Section 14 硬件开销测量方案

## 目标

依据 [model.md](./model.md) 第 14 节的 window-count direct 公式，建立一套可综合、可回放、可复现的硬件开销测量流程，回答以下问题：

1. UACC 相对无 UACC 系统增加多少状态、组合逻辑、面积、功耗和控制器计算延迟；
2. Section 14 拥塞模型相对已有 ATD/分配器增加多少开销；
3. 不同核心数、queueing domain 数、packet class 数、定点位宽和除法器实现如何影响开销；
4. allocator 是否能在下一个 profiling window 开始前完成全部候选计算，并且不进入 packet data-path critical path；
5. 选定的 Q-format、取整、饱和和 reciprocal approximation 是否保持浮点模型的预测与 allocation 决策。

当前 gem5 代码中的 `double`、`long double`、`std::vector` 和 `std::map` 是功能仿真实现，不能用 C++ 对象大小或宿主机运行时间代表硬件开销。测量对象必须是与 Section 14 等价的参数化定点 RTL；gem5 负责生成真实活动 trace 和黄金结果。

## 测量口径

### 三个必须分开报告的设计点

| 设计点 | 包含内容 | 用途 |
|---|---|---|
| B0：No-UACC | 不含 ATD、动态分配器和 queue model | 系统总开销基线 |
| B1：UACC-Base | ATD、reuse histogram、候选 packet-count 生成、固定成本、容量收益和 allocation 控制；不含 Section 14 queue collector/predictor | 隔离拥塞模型增量 |
| B2：UACC-Section14 | B1 + 每 domain 统计、CA²、feedback、R0/R1/R2、利用率 guard、direct queue cost 和 backpressure guard | 最终实现 |

必须报告：

- `B2 - B1`：Section 14 拥塞模型增量；
- `B2 - B0`：完整 UACC 开销；
- B2 占单核、LLC slice 和目标芯片面积/功耗的比例；
- ATD RAM/寄存器与 Section 14 逻辑分项，避免 ATD 容量掩盖公式本身的成本。

`mg1` 和 `gg1-feedback` 当前是浮点功能路径，只用于数值/决策比较。除非另行实现等价定点 RTL，否则不把它们的 C++ 开销与 B2 做面积或功耗对比。

### 参数化规模

首轮覆盖以下矩阵：

| 参数 | 主配置 | 扩展点 |
|---|---:|---|
| 核心数 P | 4 | 2、8、16 |
| 独立方向数 | request + response | 固定为 2 |
| queueing domains D | `2P` | 增加 1 个共享 domain 的敏感性点 |
| packet classes J | 4 | 2、4、8 |
| lookahead deltas | `{1,2,4}` | `{1}`、`{1,2,4,8}` |
| profiling window T | 100,000 cycles | 10k、50k、200k、1M |
| 最大 remote ways | 与论文主配置一致 | 8、16、32 |

在当前“每核独立 SerialLink、请求/响应分离”的拓扑下，一个核心候选只改变该核心对应的两个 domain。RTL 应缓存 current cost，并仅重算受影响的 domain；共享 xbar/link 配置则额外重算对应的共享 domain。

## 硬件设计拆分

### 每 packet 统计路径

每个 queueing domain 维护：

- `N`、`M`、`S_A`、`S_A2`；
- J 个 packet-class count `n_j`；
- `S_W`、buffer-full、backpressure；
- `last_arrival`、有效位和 occupancy maximum；
- 双 bank 或 snapshot bank，使窗口切换与后台候选计算互不阻塞。

packet 到达时只进行计数、减法、平方和饱和累加。乘法器可由所有 domain 时间复用，但必须验证同一周期多 domain 事件的最大接收率；若接口可能每周期接收多个事件，则使用小型 event FIFO 或为入口复制累加逻辑，并把该成本计入结果。

### 窗口边界计算路径

按 Section 14 固定执行：

1. `CA2 = max(0, M*S_A2/S_A^2 - 1)`，更新 shift-EWMA；
2. 用已完成窗口的 `S_W/Qmodel_current` 更新 beta shift-EWMA；
3. 从 ATD 结果构造候选 class counts；
4. 累加 `R0 = sum(n_j)`、`R1 = sum(n_j*S_j)`、`R2 = sum(n_j*S_j^2)`；
5. 用 `R1 < rho_max*T` 做无除法 guard；
6. 计算

   `Qmodel = [R1^2*(CA2eff-1) + R0*R2] / [2*(T-R1)]`；

7. 乘 beta、计算固定成本/容量收益/MU，并比较最高正 score；
8. 提交最多一个 way，清除旧 bank。

首版采用共享的乘法器和 reciprocal/divider。需要同时综合以下实现点：

- DIV-A：迭代整数除法器，面积优先；
- DIV-B：归一化 + reciprocal LUT + 一次修正乘法，吞吐优先；
- DIV-C：若有可用 HLS/PDK IP，作为商用工具参考点。

EWMA 和 `{1,2,4}` lookahead division 只使用算术右移。候选计算是窗口边界后台任务，不允许组合路径连接到 cache/D2D packet ready/valid。

## 位宽与定点格式

### 先由约束推导整数位宽

不得直接把 gem5 的 64-bit/浮点类型搬入 RTL。对每个状态定义物理上界：

- `Nmax = window 内该 domain 最大可接收 packet 数`；
- `Amax = 可表示的最大 inter-arrival cycles`；
- `Wmax = 单 packet 可累计的最大 queue-wait cycles`；
- `Smax = 最大 packet service cycles`。

由此推导：

- `wN = ceil(log2(Nmax+1))`；
- `wSA = ceil(log2(Mmax*Amax+1))`；
- `wSA2 = ceil(log2(Mmax*Amax^2+1))`；
- `wSW = ceil(log2(Nmax*Wmax+1))`；
- `wR1 = ceil(log2(Nmax*Smax+1))`；
- `wR2 = ceil(log2(Nmax*Smax^2+1))`。

所有中间乘积必须按数学上界扩展后再统一 round/saturate。报告中同时给出“理论位数”和“综合后寄存器位数”，并记录真实 trace 的最大值及 headroom。

### Q-format sweep

对 `CA2`、beta 和 reciprocal 做离线 sweep，候选集合至少包括：

- CA²/beta：Q4.8、Q6.10、Q8.16；
- reciprocal：12、16、20 个 fractional bits；
- rounding：truncate 与 round-to-nearest-even；
- saturation：逐级饱和与仅最终饱和。

选择满足功能门槛的最小格式。默认功能门槛为：所有 directed boundary tests 决策完全一致；完整 trace 中 allocation 决策一致率不低于 99.9%，且平均/最大 objective error、饱和次数和最坏误差窗口全部公开。若 99.9% 未达到，不降低门槛，而是增加位宽或 reciprocal 精度。

## 实现与数据流

### 建议新增文件

- `uacc/hw/trace_schema.md`：packet/window/candidate trace 格式和单位；
- `uacc/hw/export_uacc_trace.py`：将 gem5 统计转换为可回放输入，或在需要时增加轻量 gem5 trace hook；
- `uacc/hw/fixed_point_model.py`：逐位等价黄金模型和格式 sweep；
- `uacc/hw/rtl/`：collector、candidate engine、divider、top 和 testbench；
- `uacc/hw/run_synthesis.py`：统一综合参数、工具版本和结果抽取；
- `uacc/hw/report.py`：生成 CSV/JSON、表格和图；
- `tests/pyunit/uacc/pyunit_uacc_fixed_point.py`：定点公式与决策测试；
- `tests/verilog/uacc/`：RTL directed/random differential tests。

若最终使用不同目录，应保持功能模型、RTL、trace、脚本和生成结果分离；综合产物和大规模 VCD 不提交仓库。

### Trace 必须包含

每条 packet 事件至少包含：domain、window、enqueue cycle、class、service cycles、observed wait、occupancy、buffer-full 和 backpressure。每个窗口包含：ATD/命中预测所需计数、当前 allocation、合法候选、浮点 Section 14 的 R0/R1/R2、CA²、beta、Qmodel、MU 和最终选择。

trace 必须覆盖：

- idle/低负载；
- Poisson 或规则流量；
- on/off burst；
- 高利用率但仍低于 `rho_max`；
- 饱和候选；
- request/response/writeback 混合；
- SPEC/真实 workload 的代表性 LP、BP、HP mixes。

## 测量流程

### 1. 功能和逐位一致性

先让定点 Python model 与 `uacc/util/uacc_gg1_sim.py`/gem5 浮点 Section 14 对齐，再让 RTL 与定点 model 逐 cycle 对齐。测试包括 `M=0`、`S_A=0`、`R1=0`、`R1` 接近 `rho_max*T`、最大 CA²、最大 beta、计数器溢出、负 MU、并列 score 和窗口切换。

输出：每种格式的 prediction error、objective error、allocation agreement、oracle regret、saturation count 和第一处 mismatch 的完整输入。

### 2. 静态存储开销

用参数公式和综合报告分别统计：

- 每 domain collector bits；
- EWMA/feedback state bits；
- packet-class constant table bits；
- ATD/reuse histogram bits；
- allocation/controller bits；
- event FIFO 或双 bank bits。

小数组优先同时评估 flip-flop 和 SRAM/register-file mapping；面积表必须注明是否包含 memory macro，不能只报告标准单元面积。

### 3. 综合面积与时序

主结果使用论文目标工艺库、PVT、电压和控制器目标频率；若目标库尚未确定，先用公开标准单元库做趋势结果，并明确其不能代表流片数值。所有 B0/B1/B2 使用完全相同的：

- 工艺库与 corner；
- clock/IO delay/uncertainty；
- synthesis effort 和 hierarchy/flatten 设置；
- memory macro 假设；
- clock gating 设置。

至少报告 total cell area、combinational area、sequential area、memory area、等效门数、WNS/TNS、Fmax、关键路径模块和 divider latency。若做布局布线，额外报告 post-route area、wire/parasitic 后时序和三次 placer seed 的 median/range。

### 4. 动态与静态功耗

用 gem5 trace 驱动 RTL，生成 VCD/SAIF，至少测三种活动：idle、真实 workload median、synthetic worst burst。报告：

- leakage power；
- clock、sequential、combinational 和 memory dynamic power；
- 平均功耗与峰值窗口功耗；
- energy/packet、energy/profile-window、energy/allocation decision；
- B2-B1 增量，以及相对 LLC/芯片的比例。

idle trace 用于验证 clock gating；不允许用随机 toggle rate 替代全部主结果。若只能得到 synthesis-level power，必须标明未包含 clock-tree 和布线寄生。

### 5. 窗口计算预算

对最坏合法配置计算并实测：

- 每窗口候选数 `K <= P * |lookahead_deltas|`；
- CA²/feedback 更新周期；
- 每候选受影响 domain 数；
- divider initiation interval 和 latency；
- 总 allocator cycles、平均 cycles、busy ratio 和最大 event FIFO occupancy。

硬门槛是 `allocator_cycles < T`，并留出明确 headroom；同时证明 packet collector 在 allocator 工作时仍能无丢失接收事件。若共享一个 divider 无法满足最小 T，则比较提高流水化、复制 divider 或降低候选并行度三种方案的面积/功耗权衡。

### 6. 可扩展性和消融

以 P、D、J、T、Q-format 和 divider 方案为横轴，报告面积、功耗、Fmax 和窗口周期。额外综合：

- 无 feedback；
- 无 CA²（固定为 1）；
- 无 occupancy/backpressure guard；
- 单 bank 与双 bank；
- 每 domain 算术单元与全局共享算术单元。

该消融用于说明开销来自状态、divider、burst/feedback 还是候选调度，不用于改变最终公式。

## 验收标准

- AC-1：建立明确且可审计的 B0/B1/B2 边界。
  - Positive：报告能单独给出 Section 14 增量和完整 UACC 开销。
  - Negative：只给 C++ `sizeof`、宿主机执行时间或只有 B2 绝对面积时，不通过。
- AC-2：定点 model、RTL 和浮点 Section 14 在 directed tests 上决策完全一致。
  - Positive：正常、空窗口、饱和边界、最大 burst/feedback 均通过。
  - Negative：除零、wrap-around、未定义 tie-break 或读取未来窗口数据时，不通过。
- AC-3：选定最小 Q-format 在完整 trace 上达到至少 99.9% allocation agreement，并报告全部误差和饱和事件。
  - Positive：误差脚本可从保存的 trace 重现结果。
  - Negative：只报告均值、过滤 mismatch 或 silent saturation 时，不通过。
- AC-4：综合结果可复现。
  - Positive：脚本记录 RTL commit、参数、工具/库版本、约束、corner 和随机 seed。
  - Negative：B0/B1/B2 使用不同约束或未计 memory macro 时，不通过。
- AC-5：真实活动驱动的功耗结果完整。
  - Positive：idle、真实 workload、worst burst 均有动态/静态和分项结果。
  - Negative：仅使用默认 toggle rate 或只报告 total power 时，不通过。
- AC-6：最坏配置在最小 profiling window 内完成，且 packet 路径不受阻塞。
  - Positive：`allocator_cycles < T`，窗口切换期间事件无丢失，关键路径不连接 packet ready/valid。
  - Negative：依靠延迟窗口、丢弃事件或修改下一窗口 trace 才完成时，不通过。
- AC-7：最终表格同时给出绝对量、相对量和 scaling。
  - Positive：包含 bits、mm²/GE、mW、pJ、Fmax、cycles/window 及 B2-B1/B2-B0。
  - Negative：只用“开销很小”或仅给百分比时，不通过。

## 实施顺序

1. 冻结测量边界、主配置、工艺/频率口径和 trace schema；
2. 实现位宽上界计算器及定点 bit-accurate model，完成格式 sweep；
3. 从 gem5 导出 packet/window/candidate trace，并锁定黄金决策；
4. 实现 collector、双 bank、共享 candidate engine 和 divider RTL；
5. 完成 Python↔RTL differential test 和 overflow/boundary tests；
6. 综合 B0/B1/B2 与 divider/共享方案，收集面积、时序和静态功耗；
7. 用 synthetic 与真实 trace 生成功耗活动，测动态功耗和窗口周期；
8. 完成 P/D/J/T/Q-format scaling、消融和结果复核；
9. 形成论文主表、可扩展性图、实现框图及 reproducibility manifest。

## 最终交付物

- 参数化可综合 RTL 和逐位定点黄金模型；
- gem5 trace 导出与回放工具；
- 自动综合/功耗/报告脚本；
- 原始 CSV/JSON、工具 manifest 和错误窗口记录；
- 一张状态位数表、一张 B0/B1/B2 PPA 主表；
- P/D/J scaling 图、Q-format 精度-面积图、divider 面积-周期图；
- 对论文可直接使用的结论：Section 14 增量、完整 UACC 开销、最坏窗口 headroom，以及结果适用的工艺和配置边界。

## 当前实现状态

Section 14 的 HLS 正确性原型已在 `uacc/hw/hls/` 拆成三个可独立综合的 top：

| top | 已实现范围 |
|---|---|
| `uacc_collector` | 每 domain 的 `N/M/SA/SA2/n_j/SW`、occupancy maximum、buffer-full/backpressure、跨窗口 arrival history、饱和与 overflow |
| `uacc_cost` | `R1 < rho_max*T` guard、direct queue-cost、beta 修正、128-bit numerator、Q16 reciprocal 和显式输出饱和 |
| `uacc_allocator` | CA² 与 beta shift-EWMA、`R0/R1/R2`、固定成本/容量收益/MU、`{1,2,4}` score、occupancy/backpressure guard 和最高正候选选择 |

allocator 接收 ATD 已生成的候选绝对 packet-class count，候选 0 表示当前 allocation；ATD/reuse histogram 本身和最终 cache-way commit 是系统级 B1 模块，不在本算术原型内。collector 的 `SnapshotAndReset` 负责窗口计数清零并保留计算下一次 inter-arrival 所需的最后到达时刻。

2026-07-15 使用 Windows Vitis HLS 2019.2 从 WSL 完整重跑三个工程，结果为：

| top | XSIM C/Verilog cosim | RTL 实测 latency | xc7z020 HLS 估计 | 估计周期 |
|---|---:|---:|---:|---:|
| cost | 2009/2009 PASS | 44 cycles | 0 BRAM、26 DSP、4996 FF、3555 LUT | 3.853 ns |
| collector | 19/19 PASS | 12--36 cycles | 2 BRAM、4 DSP、2776 FF、2023 LUT | 3.530 ns |
| allocator | 265/265 PASS | 1012--1440 cycles | 0 BRAM、46 DSP、18593 FF、10028 LUT | 3.853 ns |

三套生成 Verilog 均通过 Icarus Verilog 独立编译。allocator RTL 中只有一个共享 direct-cost core；CA² 和 feedback beta 进一步共用一个逐位精确的 136/128-bit restoring divider，替代两套完全空间展开的 HLS divider。其 HLS 最坏 latency 估计为 62226 cycles，在默认 `T=100000` 下剩余 37774 cycles（37.8%），但不满足 `T=10000` 敏感性点。CA²/beta 采用 Q4.8，direct queue-cost 使用 32 项 Q16 reciprocal LUT 和一次 Newton 修正；2000 组 deterministic-random cost 测试仍满足相对 exact 128-bit division 不超过 0.03% 的误差门槛，共享 divider 另通过 256 组 deterministic-random CA² 向量与截断整数除法的逐位一致性检查。

100-seed synthetic allocation sweep 中，Q4.8/R16 相对浮点 window-count 保持 100% allocation agreement，无 factor saturation，candidate-objective MAE 为 0.0030、最大误差为 0.0138，最小参考决策 margin 为 0.506。Q4.8/R12 同样保持 100% allocation agreement，但 standalone cost 向量上的相对误差超过既有 0.03% 门槛，故不采用。

相对原两套 CA²/beta HLS divider 的 Q8.16 allocator（80 DSP、30473 FF、12639 LUT），Q4.8/R16 共享 rolled-divider 版本的 DSP 减少 42.5%、FF 减少 39.0%、LUT 减少 20.7%，代价是最坏 latency 从 4573 增至 62226 cycles。相对 Q8.16/R20 共享-divider 点，它进一步减少 4.2% DSP、17.3% FF、20.5% LUT，并将最坏 latency 降低 13.5%。这些是 FPGA 映射权衡，不能直接推断 ASIC 面积变化；ASIC 测量时必须使用相同库和约束比较各设计点。

2026-07-16 完成固定 R16 与共享 rolled-divider 架构下的 F1/F2/F4 工程 sweep。10,000-seed synthetic accuracy 中，F1/F2/F4 allocation agreement 分别为 99.750%、99.990% 和 100%，mismatch 为 25/1/0，三组均无 factor saturation。对应 ASAP7 post-global-route allocator 面积为 18356.7/18551.0/18431.8 um^2，Fmax 为 397.006/401.602/421.680 MHz，默认活动率功耗为 165.515/168.344/110.204 mW，三组 setup/hold TNS 均为 0。受综合调度和映射影响，PPA 不随小数位宽单调变化；F1 相对 F4 仅节省 0.41% 面积但产生更多决策误差，因此当前工程选择应优先 F4。完整表与适用边界见 `uacc/hw/openroad/asap7/precision_sweep_results_2026-07-16.md`。

同日进一步完成固定 Q4.4/R16、仅改变整数动态范围的软件预仿真。位宽不再由 synthetic trace 最大值裁剪，而是按 `Tmax/Nmax`、8 domains、最大 32-cycle service、4096-cycle wait、512-cycle fixed latency/gain 的工程上界推导。默认 W100K 点将 count、R1、R2、total-cost integer 和 shared-divider integer width 分别从 legacy 的 32/56/80/84/128 bits 降至 17/22/27/34/51 bits。10,000-seed、100000-cycle 扫描中，W100K/W200K/W1M/legacy-wide 均为 100% allocation agreement、0 saturation、0 regret；W100K 是默认窗口的最窄一致点。据此选择 W100K、W1M 和 legacy-wide 做受控 HLS/PPA 比较，并在三组中保留相同语义输出集合。完整结果见 `uacc/hw/range_width_sweep_results_2026-07-16.md`。

随后完成 W100K/W1M/legacy-wide 三点 HLS 与 ASAP7 扫描。三组均通过 C simulation、265/265 C/RTL cosim 和独立 Icarus 编译。W100K allocator 的 HLS 资源为 26 DSP、6773 FF、6184 LUT，最坏 29898 cycles；相对 legacy-wide 的 46 DSP、17714 FF、10573 LUT、60986 cycles，分别减少 43.5%、61.8%、41.5% 和 51.0%，并在默认 100K window 留出 70.1% 最坏周期余量。相同 ASAP7 RVT 0.77 V、5 ns、post-global-route 条件下，W100K/W1M/legacy-wide allocator 面积分别为 5210.88/5952.87/18462.10 um^2，Fmax 为 474.548/476.146/416.705 MHz，setup/hold TNS 均为 0；W100K 相对 legacy-wide 面积下降 71.78%。加入未缩窄的既有 collector 后，W100K controller 投影为 0.009344 mm^2、456.775 MHz。默认活动率功耗不可作为 workload 结论；此外 Vitis 随位宽改变 memory-port packing，collector 的 idle-gap clamp、packet-class storage 和 event ingress 仍需解决。完整结果与边界见 `uacc/hw/range_width_sweep_results_2026-07-16.md`。

为给出明确的失败边界，又加入同为 100K window 的 Lossy16 工程点。这里的“16”不是全设计统一 16 bits：count/window 保持 17 bits、R2 为 20 bits、shared divider 仍为 51 bits；主要实验变量是 R1/R2 traffic sum、per-domain cost、total cost/signed score 和 shared-divider 的整数动态范围。Lossy16 将 R1/R2 压至 16/20 bits，cost/total/score integer 压至 20/23/24 bits；W100K 和 W1M 分别是按 100K 和 1M 最大 window/packet 上界一致推导的全套位宽，legacy-wide 则保留原型的过宽类型。W1M 的 PPA 和精度仍在相同 100K workload 下测量，1M 表示硬件支持范围而非本次仿真时长。10,000-seed diverse stress 扫描中，Lossy16 对理想 oracle 的 allocation agreement 为 74.410%，对安全 W100K 为 74.170%，发生 47670 次 R1 和 33450 次 R2 saturation；W100K/W1M/legacy-wide 彼此 100% 一致且无 saturation，对 oracle 均为 99.410%。seed 1 中候选 7 的 R1=65688 和 R2=1755488 超过 Lossy16 上限，受保护实现将该候选判 invalid，决策从 `[2,0]` 退化为 `[0,0]`，oracle regret 为 175447.575 cycles/window。Lossy16 通过 266/266 C/RTL cosim 和 Icarus；其 HLS 为 19 DSP、5390 FF、5130 LUT、最坏 29469 cycles，ASAP7 allocator 为 4326.72 um^2、467.940 MHz。它相对 W100K 仅节省 16.97% allocator area 和 9.46% projected controller area，却丢失约四分之一决策，因此只保留为失败对照，不作为候选实现。

以上已经包含公开 ASAP7 库的 post-global-route 面积/时序估计，但仍不是详细布线或 signoff 结论。当前 collector 为紧凑存储结构，综合事务 interval 为 13--37 cycles；它不能直接反压 packet ready/valid。ASIC 集成前必须实现独立 event FIFO 或复制入口计数器，用 trace 的最坏 burst 验证 FIFO 深度与无丢失接收。软件侧已有 bit-accurate synthetic differential model，后续仍需真实 trace 回放、collector 位宽与 idle-gap 修正、B0/B1/B2 集成、memory macro 映射和活动驱动功耗测量。

## 范围边界

最小可接受范围是完成 B1/B2 的定点 RTL、4 核主配置、公开库综合、真实 trace 活动功耗和逐位验证。完整范围再加入 B0 系统归一化、2/8/16 核 scaling、目标 PDK/post-route、多个 divider 和完整 UACC/LLC/芯片占比。

本轮不要求实现 D2D PHY、AXI channel、Ruby/Garnet router 或整个 LLC data array RTL；这些模块只作为面积/功耗归一化参照。RTL 和注释使用硬件功能命名，不写入 `AC-*`、阶段或计划术语。
