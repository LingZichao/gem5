# UCP 与 G/G/1 算法局部消融

本实验在独立 Python 虚拟环境中重放相同的阶段变化 miss trace。UCP 仅根据 ATD 容量收益分配远端 ways；另外两种策略分别加入 G/G/1 排队成本，以及实测等待时间驱动的反馈校准。

Measured-delay oracle 在每个窗口枚举全部合法 allocation，并使用 offered-arrival 到 service-start 的真实有限 FIFO 阻塞时间评价候选。在线策略只能读取此前已完成窗口，因此 oracle 是不可在线实现的 hindsight 上界。

## 实验设置

- 4 核，4 个远端 ways，profiling window 为 2000 cycles；
- D2D bandwidth=8 bytes/cycle，buffer depth=8，rho_max=0.9；
- hidden contention 阶段真实带宽系数为 0.4，到达流量保持不变；
- 连续 trace 包含 steady、hidden contention 和 recovery 三个阶段；
- 每个负载点使用 12 个随机 seed，阴影表示 95% 置信区间。

## Case 构造特征

该 case 专门用于隔离 feedback 对“解析模型持续低估”的修正作用，不是通过改变三种策略的输入 trace 来制造差异。

- trace 共 40 个 profiling windows：窗口 0--7 为 steady，窗口 8--31 注入 hidden contention，窗口 32--39 恢复正常；
- 三种策略使用完全相同的 miss arrival、ATD hit-probability 和候选 allocation 集合；hidden contention 不改变到达率、packet size 或容量收益；
- 预测器始终假设 D2D 带宽为 8 bytes/cycle，但 hidden contention 阶段真实 FIFO 服务带宽降为 `0.4 x 8 = 3.2 bytes/cycle`，用来模拟未显式建模的仲裁、共享链路争用或服务暂停；
- 实际 queue cost 使用 offered-arrival 到 service-start 的总等待时间，因此 finite-buffer backpressure 也会进入观测；
- feedback 只使用已经完成窗口的实测等待时间，通过 `alpha=0.25` 的 EWMA 更新 `beta`，并将其限制在 `[1, 4]`；策略不能读取当前窗口未来信息；
- oracle 可以查看当前窗口的真实 FIFO 结果并枚举全部合法 allocation，因此仅作为 hindsight upper bound，不属于在线策略。

因此，该 case 的因果关系是：隐藏服务争用使裸 G/G/1 低估 queue penalty，实测等待使 feedback 提高 `beta`，随后完整策略改变 allocation 并降低 backpressure。该构造用于机制验证，不应直接解释为真实硬件带宽下降的定量预测。

## 消融结果

图 (a) 给出真实累计效用相对 oracle 的比例，图 (b) 给出每千 packet 触发的 backpressure 次数，图 (c) 展示代表性 trace 上逐窗口的 utility/oracle。

## 图中坐标含义

- **图 (a)：Utility / oracle。** 横轴为标称参考利用率 \(\rho_{\mathrm{ref}}\)，由固定初始 allocation 下的标称 D2D 服务统计得到，用于表示统一负载水平。纵轴为整段 trace 的真实累计效用与 measured-delay oracle 累计效用之比，数值越接近 1 越好。灰色、青绿色和珊瑚色曲线分别表示 UCP、UCP + G/G/1 和 UCP + G/G/1 + feedback；阴影为不同 seed 的 95% 置信区间。
- **图 (b)：BP / 1K。** 横轴仍为标称参考利用率 \(\rho_{\mathrm{ref}}\)。纵轴为每 1,000 个 packet 触发的 backpressure 次数，数值越低表示有限 FIFO 下的拥塞压力越小。该指标使用真实 FIFO 观测，不是 G/G/1 的预测值，因此可以直接衡量策略选择造成的实际排队代价。
- **图 (c)：Window utility。** 横轴为 profiling window 编号，窗口 0--7、8--31 和 32--39 分别对应 steady、hidden contention 和 recovery 阶段；竖直虚线表示阶段边界。纵轴为该窗口真实 utility 与该窗口 oracle utility 的比值，黑色水平参考线为 oracle=1。曲线低于 1 表示策略在该窗口没有达到 hindsight 最优；红色 feedback 曲线在争用阶段回升，表示实测等待反馈已经改变了后续 allocation。

图例中的 marker 用于区分策略：圆点为 UCP，方点为不带 feedback 的 G/G/1，空心菱形为带 feedback 的完整策略。oracle 只在图 (c) 中作为逐窗口参考曲线绘制；图 (a) 和图 (b) 中的 oracle 已经分别进入归一化分母和对照指标定义。

这里的 \(\rho_{\mathrm{ref}}\) 仅用于横轴负载归一化；它不等同于在线 candidate allocation 使用的 \(\rho\)，后者才与 \(\rho_{\max}=0.9\) 的饱和 guard 直接比较。

在最高负载点（\(\rho_{\mathrm{ref}}=0.426\)）下，UCP 的 utility/oracle 为 0.352，加入 G/G/1 后提高到 0.642。同时，每千 packet 的 backpressure 从 288.2 次降至 208.1 次。该结果说明纯 UCP 只考虑容量收益，在 burst 阶段可能过度扩张远端容量；G/G/1 queue penalty 能够显式计入共享链路外部性。

feedback 的最大增益出现在 \(\rho_{\mathrm{ref}}=0.426\)：utility/oracle 从 0.642 提高到 0.875，backpressure/1K packets 从 208.1 降至 96.5。这说明 feedback 在未建模争用使解析模型持续低估时能够改变 allocation；在极高负载下两条曲线重新接近，表明反馈不能替代饱和保护。

## 汇总

| 策略 | 平均 utility/oracle | 平均最终 regret | 平均 backpressure/1K packets |
|---|---:|---:|---:|
| UCP | 0.748 | 74.402 | 90.7 |
| UCP + G/G/1 | 0.848 | 43.763 | 63.8 |
| UCP + G/G/1 + feedback | 0.918 | 21.566 | 30.4 |

## 边界

该实验用于隔离 allocator 的局部机制，不替代完整系统性能实验。虚拟环境固定了 ATD hit-probability 曲线、D2D 服务分布和 FIFO 拓扑，因此论文中应将结果表述为受控机制验证，而不是 SPEC 或 gem5 IPC 结论。

原始逐 seed 数据位于 `sweep.csv`，图 (c) 的逐窗口数据位于 `trace.csv`。
