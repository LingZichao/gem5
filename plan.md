# gem5 UACC 仿真框架实现计划

## Summary

当前状态：已完成可运行、可验证的 classic-timing UACC MVP，并将 model.md 的
G/G/1+feedback 和 Section 14 direct queue-cost 两条路径接入 gem5
`UACCController`。实现覆盖远端 cache、按核容量 partition、静态/动态
allocation、真实 `SerialLink` request/response queue 观测，以及 host-side
line store 与 remote cache 的 atomic line swap 语义。它还不是论文 Figure 4
的硬件级完整实现，也不声称已经完成 Ruby/Garnet、真实 CPU hierarchy 或
共享 xbar 的独立 observer。

已阅读 [acm-sigconf.pdf](/home/ling/gem5/acm-sigconf.pdf)。论文核心包括：

- UACC 作为私有 L2 的远端 way 扩展，而非替换 LLC。
- 本地缓存未命中后，按需访问远端 UACC。
- 通过 ATD/utility monitor 统计重用距离。
- 使用距离折损和简化 queue-wait penalty 进行 congestion-aware lookahead allocation；G/G/1 的 burstiness 统计和 measured feedback 仍是后续工作。
- 重点验证缓存压力、D2D 延迟/带宽和多核拥塞。

首版采用 classic timing MVP、在线分配器、合成负载和 SE 微基准；Ruby/Garnet 和完整 2D mesh 作为后续扩展。

## 核心架构

数据流：

```text
CPU/L1
  ↓
Private L2
  ↓
UACCSelector ── bypass ───────────────→ SystemXBar/Memory
      │
      └── remote ─→ SerialLink → UACCXBar → UACCCache → SystemXBar/Memory
                              ↑
                        UACCController
```

新增 `src/mem/uacc/` 子系统：

- `UACCSelector`
  - 每个核心一个。
  - 根据当前 `allocation[core]` 在远端路径和普通内存路径之间选择。
  - 维护请求到路径的映射，处理 timing retry、response、atomic 和 functional 请求。
  - 转发下游 snoop，保证现有 coherence 行为不变。
  - 在请求上附加 `UACCRequestExtension(core_id)`。
  - atomic-swap 模式下维护一个 host-side line store，记录 victim、dirty/writable 状态，并处理 timing/atomic/functional response 和 dirty writeback。

- `UACCCache`
  - 继承现有 `Cache`。
  - 作为共享远端缓存连接到 SystemXBar。
  - 复用现有 MSHR、替换策略、填充和写回逻辑。
  - 通过 `Cache::access` 记录远端 lookup、hit、miss 和访问延迟。
  - 复用普通 cache fill，并在带有 `atomic_swap_probe` 的远端命中上将 remote line 与 host victim line 交换。
  - 当前交换是软件模型化的顺序 host lookup + remote probe；尚未实现论文 Figure 4 的真正并行 tag probe。

- `UACCController`
  - 按 profiling window 调度分配器。
  - 保存每核 allocation、ATD 统计和 D2D 参数。
  - 接收 `UACCSelector` 的本地 miss 事件和 `UACCCache` 的远端访问结果。
  - 每个窗口重新计算 allocation，并更新 selector 与远端缓存策略。

- `UACCPartitionManager` / `UACCCapacityPartitioningPolicy`
  - 从 `UACCRequestExtension` 提取 core ID。
  - 以“远端 way 数 × cache set 数”为每核容量上限。
  - 支持运行时更新容量。
  - allocation 为 0 时禁止远端路径，并避免远端缓存继续接纳该核心的新 block。

D2D 网络首版复用 gem5 现有组件：

- 每核使用 `SerialLink` 建模 D2D 延迟、带宽和缓冲。
- 使用共享 `NoncoherentXBar` 建模 UACC chiplet 侧的竞争。
- 每核距离通过不同 `SerialLink.delay` 和 controller 的 `distance_ns` 参数表示。
- 不在首版修改 Ruby/Garnet 或 SLICC coherence protocol。
- `configs/example/uacc.py` 提供 AIB、BoW、EMIB、UCIe 和 custom 参数点，并将 RTT、带宽、buffer depth 映射到 `SerialLink`、`NoncoherentXBar` 和 controller 的基础 queue penalty。

## 分配器与接口

`UACCController` 暴露以下主要参数：

- `num_cores`
- `profile_interval`
- `sampled_sets`
- `profile_depth`
- `base_ways`
- `max_remote_ways`
- `lookahead_deltas`，默认 `[1, 2, 4]`
- `lower_miss_penalty_cycles`
- `distance_ns`
- `distance_discount_per_ns`，默认 `0.0053`
- `d2d_rtt`
- `d2d_bandwidth`
- `response_flits`

ATD 行为：

- 只统计进入 UACCSelector 的 demand cacheable miss。
- 跳过 prefetch、eviction、uncacheable 和维护请求。
- 每核维护采样集合的 LRU 重用距离。
- 每个窗口清空 `H_i[r]` 计数，但保留 ATD 的 LRU 状态。
- 重用距离超过 `profile_depth` 的访问计入 overflow，不参与候选收益。

候选 utility 统一换算为 CPU cycles：

```text
capacity_gain =
    miss_rate_reduction × lower_miss_penalty_cycles × distance_discount

traffic_delta =
    miss_rate × remote_hit_probability_delta × response_flits

queue_penalty =
    W(lambda + traffic_delta) - W(lambda)

utility = capacity_gain - queue_penalty
```

当前代码中的实现是基于 aggregate lambda、RTT 和 serialization ticks 的简化 queue-wait 估计；文档中的 $C_A^2$、$C_S^2$、per-link observer、EWMA 和 measured feedback 尚未接入 `UACCController`。

当前实现将 `mg1` 保留为 legacy 对照；`gg1-feedback` 和 `section14` 使用
真实 request/response queue moments、pending enqueue-window buckets、反馈和
拥塞 guard。算法每次评估多 way lookahead，但只提交一个 way；约束
`sum(allocation) <= max_remote_ways`。

容量缩小时：

- 新请求立即使用新 allocation。
- 当前 MVP 更新 partition capacity，但没有独立的 allocation-shrink eviction controller；受限 block 的主动回收、dirty writeback 排序和未完成 MSHR 延迟回收仍待实现。

## 配置与实验入口

新增 classic cache hierarchy 配置和示例脚本，支持：

- `--uacc-policy static`
- `--uacc-policy greedy`
- `--uacc-policy distance`
- `--uacc-policy congestion`
- `--atomic-swap`
- D2D interface/point、RTT、bandwidth、buffer、request size 和 traffic-generator 参数。

建议命令行参数包括：

- 核数、L1/L2/远端缓存大小和关联度
- D2D 延迟、带宽、buffer size
- profiling window 和 ATD 参数
- 固定 allocation 或动态 allocation
- `--uacc-policy`
- `--lower-miss-penalty`
- `--distance-ns`

增加一个 Python sweep 工具，用归一化容量预算生成本地容量/远端容量组合，支持论文中的 cost-equivalent design-space exploration。首版不声称复现真实 N3/N5/N7 工艺成本，只提供可配置的归一化成本模型。

## 已完成验证

- 构建 `build/X86/gem5.opt` 成功。
- plain UACC mixed read/write：39 次 local miss、25 次 remote hit、16 次 write，无 whole-line-write assertion。
- atomic-swap mixed read/write：8 次 remote hit、8 次 atomic line swap。
- 两核低带宽/有限 buffer retry stress：两路 TrafficGen 分别产生 12/11 次 retry，完成 16 次 swap，无死锁。
- dynamic congestion allocation：完成 103 个 profiling windows、93 次 allocation change。
- gem5 queue-model smoke run：`mg1`、`gg1-feedback` 和 `section14` 均可运行；
  新模型输出 request/response queue packet count、CA²、CS²、利用率、实测
  wait、预测 wait、feedback beta、occupancy 和 backpressure。
- 相同固定 cache-line 流量下，G/G/1+feedback 与 Section 14 的候选预测一致，
  与 model.md 的代数等价关系相符；二者与 legacy M/G/1 分开可切换用于消融。
- `configs/example/cache_partitioning.py` 基线回归通过。
- `git diff --check`、Python 语法检查和 `python3 util/style.py -m` 通过。

## 后续测试与扩展

- C++ 单元测试
  - lookahead allocation 的收益排序和停止条件。
  - allocation 总容量约束。
  - 距离折损和拥塞惩罚的单调性。
  - ATD 重用距离统计。
- allocation 为 0、增加、减少时的 partition 行为。
- SerialLink 无 `VALID_SIZE`/无 payload packet、跨窗口 queue attribution、
  fixed delay wait clipping、buffer-full/retry event counting。

- gem5 系统测试
  - 单核基线与 UACC-disabled 结果一致。
  - 本地 miss、远端 hit、远端 miss 三条路径均可运行。
  - allocation 为 0 时不产生 D2D remote probe。
  - 增加远端容量后远端 hit 增加、下一级访问减少。
  - 降低 D2D 带宽或增加 RTT 后拥塞延迟增加。
  - 高缓存压力核心获得更多 allocation，低压力核心保持 0 或较低 allocation。
  - 多核 dirty writeback、snoop、atomic 和 functional access 正确。
  - 有限 request/response buffer 下 retry 不丢包、不死锁。

- 回归入口
  - `TrafficGen` / `MemTest` 合成访问。
  - 小型 SE 程序。
  - 构建 `build/X86/gem5.opt` 并运行新增 UACC 测试套件。

## 当前 Assumptions 与边界

- 首版只保证 classic timing mode 的主要性能建模。
- gem5 中的“本地 L2 miss”作为论文 ATD 的 L1 miss 流近似。
- 共享 UACCCache 通过 partition policy 模拟每核私有远端容量。
- coherence protocol 不新增状态，远端 cache 直接接入现有 SystemXBar。
- 首版采用顺序 host-side lookup → D2D → UACC lookup；已实现 remote-hit 后的 host/remote line exchange 语义，但不实现论文 Figure 4 的硬件级并行 tag lookup。
- Ruby/Garnet flit 级 2D mesh、SPEC 多程序长运行和更精确的 chiplet 成本模型放入后续阶段。
- 真正的 CPU `SwapReq`/LLSC/atomic 请求交给下游 cache/memory 保持原子语义，不与 host-side line-swap 模型混用。
- 多核示例使用 gem5 全局 exit event，首个 TrafficGen 完成时可能结束整个模拟，因此多核统计不是严格的 all-generator barrier。
