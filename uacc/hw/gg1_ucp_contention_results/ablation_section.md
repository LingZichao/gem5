# UCP 与 G/G/1 算法局部消融

本实验在独立 Python 虚拟环境中重放相同的阶段变化 miss trace。UCP 仅根据 ATD 容量收益分配远端 ways；另外两种策略分别加入 G/G/1 排队成本，以及实测等待时间驱动的反馈校准。

Measured-delay oracle 在每个窗口枚举全部合法 allocation，并使用 offered-arrival 到 service-start 的真实有限 FIFO 阻塞时间评价候选。在线策略只能读取此前已完成窗口，因此 oracle 是不可在线实现的 hindsight 上界。

## 实验设置

- 4 核，4 个远端 ways，profiling window 为 2000 cycles；
- D2D bandwidth=8 bytes/cycle，buffer depth=8，rho_max=0.9；
- On/Off 与 synchronized 阶段的真实带宽系数分别为 0.45 和 0.55；
- 连续 trace 包含 steady、On/Off burst、synchronized burst 和 recovery 四个阶段；
- 每个负载点使用 12 个随机 seed，阴影表示 95% 置信区间。

## 消融结果

图 (a) 给出真实累计效用相对 oracle 的比例，图 (b) 给出每千 packet 触发的 backpressure 次数，图 (c) 展示代表性阶段变化 trace 上的累计真实效用。

在最高负载点（参考利用率 0.334）下，UCP 的 utility/oracle 为 0.268，加入 G/G/1 后提高到 0.858。同时，每千 packet 的 backpressure 从 293.2 次降至 153.7 次。该结果说明纯 UCP 只考虑容量收益，在 burst 阶段可能过度扩张远端容量；G/G/1 queue penalty 能够显式计入共享链路外部性。

feedback 的最大增益出现在参考利用率 0.246：utility/oracle 从 0.908 提高到 0.942，backpressure/1K packets 从 74.9 降至 47.5。这说明 feedback 在未建模争用使解析模型持续低估时能够改变 allocation；在极高负载下两条曲线重新接近，表明反馈不能替代饱和保护。

## 汇总

| 策略 | 平均 utility/oracle | 平均最终 regret | 平均 backpressure/1K packets |
|---|---:|---:|---:|
| UCP | 0.690 | 66.332 | 150.9 |
| UCP + G/G/1 | 0.905 | 18.594 | 74.5 |
| UCP + G/G/1 + feedback | 0.920 | 15.967 | 61.9 |

## 边界

该实验用于隔离 allocator 的局部机制，不替代完整系统性能实验。虚拟环境固定了 ATD hit-probability 曲线、D2D 服务分布和 FIFO 拓扑，因此论文中应将结果表述为受控机制验证，而不是 SPEC 或 gem5 IPC 结论。

原始逐 seed 数据位于 `sweep.csv`，图 (c) 的逐窗口数据位于 `trace.csv`。

