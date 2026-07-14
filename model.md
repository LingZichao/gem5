# UACC AXI 突发感知 D2D 拥塞模型与实验规范

## 1. 最终选择

UACC 采用以下混合模型：

> **AXI-transaction-level G/G/1 moment approximation + measured queue feedback**

本文将其称为 **Burst-Aware Moment-Based Queueing Predictor with Runtime Calibration**。

本轮论文修改采用以下明确语义：

- 到达过程 $A$：AXI transaction 开始进入被建模 D2D 瓶颈队列的过程。
- 服务过程 $G$：一个获得链路服务资格的 AXI transaction 实际占用瓶颈资源的时间分布，包括可变 burst length，但不包括排队等待时间。
- feedback：实际 enqueue-to-service delay、queue occupancy、buffer-full 和 backpressure 统计。
- 解析模型负责预测尚未执行的候选 allocation；feedback 负责校准模型无法表达的相关性和有限缓冲行为。

该模型的定位不是精确重建任意 D2D 流量的完整延迟分布，而是在线预测候选 UACC allocation 的拥塞风险，可靠判断增加远端容量是否仍有正收益，并在突发流量、有限缓冲和反压下避免把链路推入饱和区。

选择该方案的原因如下：

- 相比 M/G/1，它不再假设到达过程必然为独立泊松流，能够通过到达间隔方差显式反映 burstiness。
- 相比完整 GGeo/maximum-entropy NoC 模型，它只需要前两阶统计量，适合 profiling-window 驱动的在线控制器。
- 相比只读取当前队列状态，它能够预测尚未执行的候选 allocation。
- 实测 queue feedback 可以补偿二阶矩无法描述的长程相关性、同步 burst、有限 buffer、仲裁和 backpressure。
- 当到达流接近泊松过程时，模型自然退化为原来的 M/G/1，不会割裂原有算法思路。

该选择是本轮投稿的最终基线。只有实验表明 AXI transaction-start 的 $C_A^2$ 在所有关键配置下均接近 1，且 M/G/1 与实测队列延迟及 allocation oracle 一致时，才允许在论文中将模型降级为更简单的 M/G/1。

## 2. 与 M/G/1 和 GGeo 的关系

### 2.1 与 M/G/1 的关系

M/G/1 假设到达过程为 Poisson，服务时间服从一般分布。其平均排队等待时间为：

$$
W_q^{M/G/1}
=
\frac{\lambda E[S^2]}
     {2(1-\lambda E[S])}.
$$

令

$$
\rho=\lambda E[S],
\qquad
C_S^2=\frac{\operatorname{Var}(S)}{E[S]^2},
$$

可写成：

$$
W_q^{M/G/1}
=
\frac{\rho}{1-\rho}
\frac{1+C_S^2}{2}E[S].
$$

UACC 的新模型采用 G/G/1 的 Kingman moment approximation：

$$
W_q^{G/G/1}
\approx
\frac{\rho}{1-\rho}
\frac{C_A^2+C_S^2}{2}E[S].
$$

其中 $C_A^2$ 是到达间隔的平方变异系数。当 $C_A^2=1$ 时，该式与 M/G/1 的 Pollaczek-Khinchine 结果一致。因此，新模型可以准确表述为原 M/G/1 predictor 的 **burst-aware extension**。

### 2.2 与 GGeo 的关系

G/G/1 是队列类别：一般到达过程、一般服务时间、一个服务台。GGeo 是一种具体的离散时间到达分布。因此，GGeo/G/1 是 G/G/1 的一个具体实例，而不是与 G/G/1 同一层次的概念。

Mandal 等人在 ICCAD 2020 的工作使用 generalized geometric（GGeo）流量，以 $(\lambda,C_A)$ 表征每个流量类，并结合 maximum entropy、流量 merge/split、superposition、优先级和 deflection routing 推导 NoC 延迟。该模型适合离线分析复杂多级 NoC，但对于 UACC 的在线单瓶颈或少量瓶颈链路过重。

UACC 借鉴其核心思想——使用 $(\lambda,C_A)$ 表征 burstiness——但不拟合完整 GGeo 分布，也不进行 maximum-entropy 网络分解。UACC 只使用到达与服务过程的前两阶矩，并以实测队列反馈校准模型误差。

参考文献：

- S. K. Mandal et al., “Performance Analysis of Priority-Aware NoCs with Deflection Routing under Traffic Congestion,” ICCAD 2020, DOI: 10.1145/3400302.3415654.
- Open version: https://arxiv.org/abs/2008.03904

### 2.3 AXI burst 在模型中的位置

采用 AXI 协议后，必须区分两种 burst：

1. 单个 AXI transaction 内的多 beat burst。若链路或 bridge 从获得 grant 到 `RLAST/WLAST` 保持服务权，则 burst length 属于服务时间分布 $G$。
2. 多个独立 AXI transaction 在短时间内连续发出。该现象属于到达过程 $A$，不能通过增大服务时间吸收。

因此，AXI 使可变 burst service time 的建模更明确，但不会自动证明 transaction-start arrivals 为 Poisson。审稿人对 M/G/1 中 $M$ 的质疑仍需通过 $C_A^2$ 和实际队列数据回答。

本轮实验默认一个 AXI burst 是 D2D 数据通道上的非抢占服务单元。若实现允许在 beat/flit 边界交错不同 transaction，则 customer 必须改为实际仲裁单元，AXI burst length 转化为一组相关到达；该情况需要在实验配置和论文中单独说明。

## 3. 建模边界与基本假设

模型以每条 **有向 D2D 瓶颈链路** 为一个单服务台队列。请求方向与响应方向分别统计；若硬件明确共享同一物理带宽，则在同一服务台中合并对应流量。统计 customer 是瓶颈仲裁器实际调度的 AXI transaction，而不是抽象 CPU memory instruction。

模型假设：

- profiling window 内流量近似平稳；窗口之间允许通过 EWMA 跟踪相位变化。
- 链路采用近似 work-conserving 的仲裁。
- 首版假设 AXI burst 获得数据通道服务权后保持到 `RLAST/WLAST`；若实际 interconnect 不满足，按实际仲裁粒度重定义 customer。
- 目标是估计平均排队代价和饱和风险，不预测严格的 worst-case 或 tail latency。
- 有限 buffer、反压和未建模相关性由实测 feedback 与硬安全阈值处理。
- 最终性能结果必须来自 trace-driven/cycle-accurate simulation，而不是只依赖解析公式。

## 4. 延迟分解

远端访问延迟分为：

$$
L_{remote}
=
L_{fixed}+L_{ser}+L_{queue}.
$$

其中：

- $L_{fixed}$：D2D 传播、路由、远端 cache lookup 和其他不持续占用链路的固定延迟。
- $L_{ser}$：AXI transaction 在 D2D 瓶颈上的序列化/占用时间。
- $L_{queue}$：链路竞争导致的排队等待时间。

RTT 只能进入 $L_{fixed}$，不能作为队列服务时间。对于 AXI transaction 类型 $j$：

$$
S_j=\frac{B_{header,j}+N_{beat,j}B_{beat,j}}{BW},
$$

其中 $N_{beat}=AxLEN+1$，$B_{beat}$ 由 `AxSIZE` 给出，$BW$ 是链路 payload bandwidth。若 bridge 在 transaction 获得 grant 后的空拍期间仍锁定链路，则锁定空拍属于服务时间；若其他 transaction 可利用该空拍，则不计入该 transaction 的服务时间。

首版至少区分：

| AXI/D2D 事务类型 | 符号 | 建议建模方式 |
|---|---:|---:|
| AR/AW remote lookup | req | 单 beat 地址/控制事务 |
| R cache-line response | data | 由 `ARLEN`、`ARSIZE` 决定的 burst |
| B 或 remote-miss response | miss | 单 beat 状态事务 |
| W dirty writeback | wb | 由 `AWLEN`、`AWSIZE` 决定的 burst |

实际默认值应由 AXI data width、`AxLEN/AxSIZE` 或 D2D bridge packetization 参数给出，不能将所有事务统一视为一个 full-cache-line response。AR/AW、R、W、B 若最终共享一个物理 serializer，则作为同一服务台中的不同服务类别；若为独立通道，则分别建模。

## 5. 流量模型

设核心 $i$ 在 profiling window $T$ 内产生 $N_{miss,i}$ 次符合条件的本地 cache miss：

$$
m_i=\frac{N_{miss,i}}{T}.
$$

ATD 在分配 $k_i$ 个远端 way 时预测远端命中概率：

$$
p_i(k_i)
=
\frac{
\sum_{r=Pos_{base}}^{Pos_{base}+k_i-1}H_i[r]
}{N_{miss,i}}.
$$

对顺序 local miss → remote lookup 的首版实现，下式表示 AXI/D2D transaction 到达率，而不是 flit 到达率：

$$
\lambda_{i,req}(k_i)
=
m_i\mathbf{1}[k_i>0],
$$

$$
\lambda_{i,data}(k_i)
=
m_i p_i(k_i),
$$

$$
\lambda_{i,miss}(k_i)
=
m_i(1-p_i(k_i))\mathbf{1}[k_i>0].
$$

dirty writeback transaction rate $\lambda_{i,wb}$ 应优先从实际统计获得。对应 W burst 的服务时间由实际 burst length 决定。若候选 allocation 会引发容量回收，则应将预计回收 writeback 作为短期附加流量或由反馈安全阀处理。

从 0 way 增加到 1 way 会为所有符合条件的本地 miss 启用 remote lookup，因此存在 activation cost；从 1 way 增加到更多 way 时，lookup request rate 通常不再按相同比例增加。候选模型必须保留这一不连续性。

## 6. 服务时间矩

对链路 $\ell$，令第 $j$ 类 AXI transaction 到达率为 $\lambda_{\ell,j}$，总 transaction 到达率为：

$$
\Lambda_\ell=\sum_j\lambda_{\ell,j}.
$$

服务时间的一阶矩为：

$$
E[S_\ell]
=
\frac{\sum_j\lambda_{\ell,j}S_{\ell,j}}
     {\Lambda_\ell}.
$$

二阶矩为：

$$
E[S_\ell^2]
=
\frac{\sum_j\lambda_{\ell,j}S_{\ell,j}^2}
     {\Lambda_\ell}.
$$

服务时间平方变异系数为：

$$
C_{S,\ell}^2
=
\frac{E[S_\ell^2]-E[S_\ell]^2}
     {E[S_\ell]^2}.
$$

链路利用率直接计算为：

$$
\rho_\ell
=
\sum_j\lambda_{\ell,j}S_{\ell,j}
=
\Lambda_\ell E[S_\ell].
$$

## 7. 在线 burstiness 统计

对每条有向瓶颈链路，记录连续 AXI transaction 到达被建模队列的间隔：

$$
A_n=t_n-t_{n-1}.
$$

每个 profiling window 维护：

```text
arrival_count
last_arrival_tick
sum_interarrival
sum_interarrival_squared
```

窗口结束时计算：

$$
E[A]=\frac{\sum_n A_n}{N},
$$

$$
\operatorname{Var}(A)
=
\max\left(0,\frac{\sum_n A_n^2}{N}-E[A]^2\right),
$$

$$
C_A^2
=
\frac{\operatorname{Var}(A)}{E[A]^2}.
$$

对低样本窗口：

- 若有效间隔数少于可配置阈值 `min_arrival_samples`，沿用上一窗口的 EWMA 值。
- 系统启动时默认 $C_A^2=1$。
- 使用数值稳定的 Welford 算法或等价方法维护方差。

窗口间使用 EWMA：

$$
\overline C_{A,t}^2
=
\alpha C_{A,t}^2
+(1-\alpha)\overline C_{A,t-1}^2.
$$

为避免低估突发拥塞，默认使用：

$$
C_{A,eff}^2=\max(1,\overline C_A^2).
$$

该保守下界应作为可配置选项，以便实验比较是否允许 sub-Poisson traffic 获得较低预测延迟。

## 8. G/G/1 moment predictor

每条链路的基础预测为：

$$
W_{q,\ell}^{model}
=
\frac{\rho_\ell}{1-\rho_\ell}
\frac{C_{A,eff,\ell}^2+C_{S,\ell}^2}{2}
E[S_\ell].
$$

当 $\rho_\ell\leq0$ 时返回 0。当 $\rho_\ell$ 达到硬阈值时，不继续计算一个有限但巨大的 utility penalty，而是直接判定候选不可接受。

Kingman 公式本质上是平均等待时间近似。它不能完整捕捉长程相关性、同步 memory phases、MSHR 批量释放或有限 buffer 的离散反压，因此必须结合下一节的实测校准。

## 9. Measured queue feedback

### 9.1 实测量

链路或其相邻队列每个窗口至少统计 AXI transaction 级信息：

```text
observed_queue_delay_sum
observed_queue_delay_samples
average_queue_occupancy
maximum_queue_occupancy
buffer_full_events
retry_or_backpressure_events
```

实测平均等待时间为：

$$
W_q^{obs}
=
\frac{\sum_n(t_{service,n}-t_{arrival,n})}{N}.
$$

### 9.2 校准因子

当前流量下计算模型值 $W_q^{model,current}$，定义：

$$
\beta_t
=
\operatorname{clip}
\left(
\frac{W_{q,t}^{obs}}
     {\max(W_{q,t}^{model,current},\epsilon)},
1,\beta_{max}
\right).
$$

对 $\beta$ 使用 EWMA，并建议默认：

```text
beta_initial = 1.0
beta_max = 4.0
feedback_ewma_alpha = 0.2
```

候选 allocation 使用：

$$
\widehat W_{q,\ell}(\mathbf{k}')
=
\overline\beta_\ell
W_{q,\ell}^{model}(\mathbf{k}').
$$

实测值反映当前 allocation，不能直接作为候选 allocation 的等待时间；校准因子负责将当前模型误差外推到候选预测。

### 9.3 硬安全阈值

满足以下任一条件时拒绝扩容候选：

- $\rho_\ell(\mathbf{k}')\geq\rho_{max}$，建议初始值为 0.90。
- 当前平均或最大 queue occupancy 超过配置阈值。
- `buffer_full_events` 或 `retry_or_backpressure_events` 超过阈值。
- 预测量出现 NaN、无穷值或无有效统计样本且链路已经处于高利用率。

持续多个窗口超过反馈阈值时，控制器应允许收缩 allocation，而不仅是停止继续扩容。

## 10. 候选 allocation 的统一成本模型

所有收益与代价统一为 cycles/s，避免原公式中 miss-rate reduction 与 queueing time 直接相减的量纲错误。

### 10.1 容量收益

候选 $k_i\rightarrow k_i+\Delta$ 带来的新增远端命中率：

$$
\Delta h_i
=
m_i[p_i(k_i+\Delta)-p_i(k_i)].
$$

若避免一次下层访问节省 $L_{lower,i}$ 个 CPU cycles：

$$
G_i
=
\Delta h_iL_{lower,i}.
$$

$G_i$ 的单位为 cycles/s。距离影响应进入真实的固定远端延迟，或作为经过实验标定的收益折扣；不应与排队模型重复计算同一延迟。

### 10.2 固定远端成本

定义 allocation vector $\mathbf{k}$ 下的固定通信成本：

$$
C_{fixed}(\mathbf{k})
=
\sum_{\ell,j}
\lambda_{\ell,j}(\mathbf{k})
L_{fixed,\ell,j}.
$$

候选增量为：

$$
\Delta C_{fixed}
=
C_{fixed}(\mathbf{k}')-C_{fixed}(\mathbf{k}).
$$

### 10.3 总排队外部性

单条链路的总排队成本为：

$$
C_{q,\ell}(\mathbf{k})
=
f_{cpu}\Lambda_\ell(\mathbf{k})
\widehat W_{q,\ell}(\mathbf{k}).
$$

整个系统的候选增量为：

$$
\Delta C_q
=
\sum_\ell
\left[
C_{q,\ell}(\mathbf{k}')-C_{q,\ell}(\mathbf{k})
\right].
$$

使用 $\Lambda W_q$ 而不是只使用 $W_q(\lambda+\Delta\lambda)-W_q(\lambda)$，从而计入新增流量对所有既有请求造成的拥塞外部性。

### 10.4 最终边际效用

$$
MU_i(\Delta)
=
G_i-\Delta C_{fixed}-\Delta C_q.
$$

只有 $MU_i(\Delta)>0$ 且所有链路均通过硬安全检查时，候选才可接受。

若算法评估多 way lookahead $\Delta>1$ 但每次只提交一个 way，候选排序默认使用：

$$
Score_i(\Delta)=\frac{MU_i(\Delta)}{\Delta},
$$

避免较大的 $\Delta$ 仅因累计收益更多而天然占优。若实验需要跨越短期零收益位置，可同时记录 cumulative utility 和 per-way score，并通过消融实验说明选择。

## 11. 分配器伪代码

```text
measure per-core miss rate m_i
estimate p_i(k) from ATD
update per-link lambda, C_A^2, C_S^2 and feedback beta

initialize allocation vector k

while total allocated ways < max_remote_ways:
    best = none

    for each core i:
        for each lookahead delta:
            candidate = k
            candidate[i] += delta

            derive request/data/miss/writeback rates for candidate

            if any candidate link violates utilization or feedback guard:
                reject candidate
                continue

            gain = added_remote_hit_rate * lower_miss_penalty
            fixed_delta = FixedCost(candidate) - FixedCost(k)
            queue_delta = QueueCost(candidate) - QueueCost(k)
            utility = gain - fixed_delta - queue_delta
            score = utility / delta

            retain candidate with highest positive score

    if no positive candidate exists:
        break

    commit one way to the selected core
    update predicted link traffic and repeat

apply allocation

if measured congestion remains above contraction threshold
for multiple windows:
    remove the remote way with the smallest retained benefit
```

## 12. gem5 实现要求

### 12.1 建议新增参数

```text
axi_data_width_bytes
axi_address_bytes
axi_response_bytes
axi_max_burst_beats
axi_burst_holds_grant
request_default_beats
data_response_default_beats
writeback_default_beats
rho_max
min_arrival_samples
arrival_ewma_alpha
feedback_ewma_alpha
feedback_beta_max
queue_occupancy_threshold
backpressure_threshold
contraction_windows
```

`response_flits` 应由明确的 AXI/D2D data width、burst beats 和 bridge framing 参数取代，或仅作为配置层兼容参数。实验配置必须输出最终使用的 transaction service-time mapping，保证论文表格中的带宽、beat width 和 burst length 可以复现。

### 12.2 建议新增统计

```text
arrivalSCV
serviceSCV
transactionArrivalRate
meanTransactionServiceTime
secondMomentTransactionServiceTime
linkUtilization
modelQueueDelay
observedQueueDelay
feedbackBeta
averageQueueOccupancy
maximumQueueOccupancy
bufferFullEvents
backpressureEvents
rejectedByUtilization
rejectedByFeedback
allocationContractions
```

建议将统计按有向链路和 AXI channel/transaction class 分组，同时提供合并后的瓶颈统计。每个 profiling window 至少输出一次 compact trace，字段为：

```text
window_id
link_id
transaction_count
lambda
CA2_raw
CA2_ewma
ES
ES2
CS2
rho
Wq_model
Wq_observed
beta
queue_occupancy_avg
queue_occupancy_max
buffer_full_events
backpressure_events
allocation_vector
```

### 12.3 当前拓扑一致性

当前配置为每核创建独立 `SerialLink`，而控制器将所有核心流量合并为一个全局 $\lambda$。两种建模语义不一致，必须选择其一：

1. 若论文假设共享 D2D 瓶颈，应将拓扑改为共享 host-side arbiter/queue 和共享 D2D link，然后使用全局或共享链路统计。
2. 若保留每核独立 D2D link，应为每条 link 单独维护 $\lambda$、$C_A^2$、$C_S^2$、$\beta$ 和 queue cost；UACC 侧共享 xbar 的竞争另行统计。

对于论文中的 congestion-aware multi-core UACC，推荐第一种共享瓶颈拓扑，后续再扩展为每条有向 2D mesh link 的独立模型。

### 12.4 观测点

到达时间应在 AXI transaction 真正进入被建模瓶颈队列时记录，服务开始时间应在 transaction 获得链路发送资格时记录，服务结束时间应在该仲裁单元完成时记录。若一个 burst 被锁定到 `RLAST/WLAST`，结束点为最后一个 beat；若允许 beat/flit 级交错，customer 与观测点必须改为实际仲裁单元。若只在 controller 收到逻辑访问事件时记录，会遗漏下游仲裁和 backpressure。

首选方案是在 `SerialLink` 或专用共享 D2D queue 中暴露轻量 observer/callback；次选方案是在 UACC D2D wrapper 中记录 enqueue、dequeue 和 retry 事件。

### 12.5 gem5 classic 到 AXI transaction 的映射

gem5 classic memory system 内部传递的是 `Packet`，当前 `SerialLink` 根据 `pkt->getSize()` 计算序列化周期，并不原生建模 AXI 的 AR/AW/R/W/B channel、`AxLEN/AxSIZE`、ID 或 `LAST`。因此，在完成映射层之前，实验只能声称是 **AXI-like transaction abstraction**，不能声称执行了完整 AXI protocol simulation。

当前实现还存在一个具体风险：read request 的 `Packet` size 表示访问的数据范围，不一定等于物理 AR channel 上的地址/控制字节数。直接对所有 request 和 response 使用 `pkt->getSize()`，可能把一个 AR request 错误地按整条 cache line 收取序列化时间。

实验前必须实现或明确以下映射：

| gem5 command/direction | AXI-like transaction | 链路服务字节数 |
|---|---|---:|
| `ReadReq` | AR | `axi_address_bytes` |
| successful read response | R burst | response payload bytes + framing |
| write/writeback request | AW + W burst | address/control 与 data 分别计费，或明确采用合并近似 |
| write acknowledgement | B | `axi_response_bytes` |
| remote miss/status response | B-like/control | `axi_response_bytes` |

推荐增加 `transferBytes(pkt, direction, phase)` 或等价 adapter，由它统一决定实际 serializer service time，不能继续让所有路径直接使用 `pkt->getSize()`。若为了首版简化而将 AW+W 合并成一个不可抢占 transaction，论文和配置必须明确该近似，并通过敏感性实验比较独立 channel 与合并 channel 的差异。

若没有实现完整 AXI channel timing，论文推荐使用以下措辞：

> We model an AXI-like transaction interface at the D2D bottleneck, including separate control and payload transfer sizes and configurable burst lengths; detailed AXI channel handshaking is abstracted by the timing-link queue.

不应使用：

> Our simulator implements a cycle-accurate AXI4 interconnect.

## 13. 验证计划

### 13.1 数学与单元测试

- $C_A^2=1$ 时 G/G/1 结果与 M/G/1 一致。
- 固定服务时间时 $C_S^2=0$，结果退化为 M/D/1 对应形式。
- 固定 $\lambda$ 时，$W_q$ 随 $C_A^2$、$C_S^2$ 和 $\rho$ 单调增加。
- $\rho\rightarrow1$ 时候选被硬阈值拒绝。
- $\beta\geq1$，并正确受到 `feedback_beta_max` 限制。
- 多种 AXI transaction/burst length 的 $E[S]$、$E[S^2]$ 和 $C_S^2$ 计算正确。
- 总 queue cost 包含既有流量的 congestion externality。

### 13.2 合成流量

至少测试：

- Poisson/geometric arrivals，验证 $C_A^2\approx1$。
- deterministic arrivals，验证低 burstiness 行为。
- on/off burst traffic。
- 连续 cache-line streaming。
- 多核同步 memory phase。
- MSHR completion burst。
- dirty writeback burst。

逐步扫描：

```text
arrival rate
burst length
on/off duration
AXI burst-length and channel mixture
D2D bandwidth
buffer depth
core count
```

### 13.3 模型准确性

比较：

1. 原 RTT-based M/G/1。
2. 修正 serialization-based M/G/1。
3. G/G/1 moment approximation。
4. G/G/1 + measured queue feedback。
5. simulator-measured queue-delay oracle。

报告：

- 平均和中位相对误差。
- 最大及 P95 低估误差。
- P95/P99 误差作为补充，不将 moment model 宣称为 tail predictor。
- allocation decision agreement with oracle。
- buffer-full/retry/backpressure 次数。
- 系统性能和是否出现低于 monolithic baseline 的退化。

对真实等待时间 $W_q^{obs}>0$ 的窗口，定义 signed relative error：

$$
e_t=\frac{W_{q,t}^{pred}-W_{q,t}^{obs}}
          {W_{q,t}^{obs}}.
$$

其中 $e_t<0$ 表示低估。单独报告 underestimation magnitude：

$$
u_t=\max(0,-e_t).
$$

低流量且 $W_q^{obs}$ 接近 0 的窗口应同时报告绝对 cycle error，避免相对误差失真。所有误差结果按利用率区间分桶：

```text
[0.0, 0.3)
[0.3, 0.6)
[0.6, 0.8)
[0.8, rho_max)
```

### 13.4 消融实验

建议包含：

```text
distance-only
M/G/1
G/G/1 moments
G/G/1 moments + feedback
measured-delay oracle
```

重点证明 feedback 不是只改善预测数值，而是减少错误 allocation、链路饱和和 baseline regression。

### 13.5 分阶段实验矩阵

为避免直接运行过大的全组合，实验按三阶段推进。

#### 阶段 A：单链路模型校准

固定 cache 行为，只验证 AXI/D2D queue：

```text
arrival process: Poisson, deterministic, on/off, batch release
target CA2: approximately 1, 2, 4, 8
AXI burst beats: 1, 4, 8, 16
utilization rho: 0.1 to 0.9
buffer depth: low, nominal, high
```

输出 M/G/1、G/G/1、G/G/1+feedback 和 oracle 的预测曲线。该阶段必须先确认观测点、单位、service boundary 和 $C_A^2/C_S^2$ 统计正确，之后才能进行系统性能实验。

#### 阶段 B：多核定向压力测试

```text
core count: 4, 16, 32, 64
traffic: streaming, synchronized phase, MSHR release, writeback burst
D2D tier: weakest, nominal, strongest
allocation policy: distance-only, M/G/1, G/G/1, G/G/1+feedback, oracle
```

重点观察少核条件下 $C_A^2$ 是否偏离 1，以及核数增加后聚合流量是否更接近 Poisson。不能预设“核数越多必然越 Poisson”，必须用数据验证。

#### 阶段 C：真实 workload 与最终性能

使用论文确定的 SPEC CPU2017 workload groups 和 LP/BP/HP mixes，保持相同 warmup、measurement region、cache budget 和 D2D tier。除性能外，保存每个窗口的 $C_A^2$、$C_S^2$、$\beta$、利用率、实测等待时间和 allocation vector。

### 13.6 投稿前成功标准

以下是本轮实验的预注册目标，而不是对模型精确性的理论保证：

- G/G/1+feedback 在高利用率窗口的 P95 underestimation 明显低于裸 M/G/1，并给出统计结果而非只展示个别曲线。
- G/G/1+feedback 的 allocation decision agreement with measured-delay oracle 目标不低于 90%。
- 相对 oracle 的最终性能差距目标控制在 2% 以内；若未达到，必须分析是 moment extrapolation、feedback lag 还是 ATD 误差导致。
- 在 burst stress tests 中，不出现由错误扩容造成的持续 buffer saturation 或明显 baseline regression。
- 相比 distance-only 和 M/G/1，新增模型必须降低 buffer-full/backpressure events；如果只改善预测误差但不改善 allocation 或性能，不应将其作为主要贡献。
- 报告窗口大小、EWMA $\alpha$、$\rho_{max}$ 和 $\beta_{max}$ 的敏感性，证明结论不依赖单一调参点。
- 报告硬件状态位数、每 transaction 更新操作和每 profiling window 计算次数。

若上述目标没有达到，不允许仅通过修改阈值隐藏结果。应依次检查：customer/服务边界是否定义错误、共享链路拓扑是否与全局 $\lambda$ 一致、反馈是否滞后、候选流量外推是否遗漏 writeback，最后再决定是否需要 GGeo/MMPP 等更复杂模型。

### 13.7 第一轮实验默认值

第一轮功能验证使用以下统一起点，后续必须做敏感性分析：

```text
profiling_window_cycles = 100000
min_arrival_samples = 32
arrival_ewma_alpha = 0.2
feedback_ewma_alpha = 0.2
feedback_beta_initial = 1.0
feedback_beta_max = 4.0
rho_max = 0.90
CA2_initial = 1.0
CA2_conservative_floor = 1.0
```

建议敏感性点：

```text
profiling_window_cycles: 25000, 100000, 400000
EWMA alpha: 0.1, 0.2, 0.5
rho_max: 0.85, 0.90, 0.95
feedback_beta_max: 2, 4, 8
```

queue occupancy、buffer-full 和 backpressure 的拒绝阈值应先由阶段 A 的 buffer-depth sweep 标定，不能在看到最终 workload 性能后反向选择。阈值选定过程和最终值需写入实验日志。

## 14. 论文表述建议

推荐表述：

> UACC employs a lightweight AXI-transaction-level G/G/1 moment approximation to estimate the congestion impact of candidate capacity allocations. The predictor characterizes each directed D2D bottleneck using the aggregate transaction arrival rate, the squared coefficient of variation of transaction inter-arrival times, and the first two moments of AXI burst service time. Since second-order statistics cannot fully capture temporally correlated transaction bursts, finite buffering, and backpressure, UACC calibrates the analytical estimate using measured queueing delay and enforces utilization and queue-occupancy guardrails. The model is used to rank allocation candidates and avoid saturation, rather than as an exact predictor of the complete latency distribution.

与 Mandal 等人关系的推荐表述：

> Prior burst-aware NoC models characterize generalized-geometric traffic using its arrival rate and inter-arrival-time variation and propagate these statistics through complex priority and deflection networks. UACC adopts the same first-two-moment characterization of burstiness, but uses a lightweight G/G/1 approximation and runtime calibration because its predictor executes online and targets D2D bottleneck allocation rather than offline reconstruction of an entire NoC queueing network.

需要避免的表述：

- “G/G/1 accurately models arbitrary bursty traffic.”
- “The analytical model captures all temporal correlations.”
- “RTT is the link service time.”
- “The predictor replaces cycle-accurate congestion simulation.”
- “Mandal et al. use a standard G/G/1 model.”

## 15. 审稿回复核心观点

对 Poisson 假设和低估 burst delay 的质疑，回复应包含：

1. 承认裸 M/G/1 在 $C_A^2>1$ 时可能低估等待时间。
2. 说明修订后通过实测 $C_A^2$ 使用 G/G/1 moment approximation。
3. 说明 $C_A^2=1$ 时退化为原 M/G/1，因此属于自然扩展。
4. 说明强时间相关性通过 measured queue delay、occupancy 和 backpressure feedback 补偿。
5. 强调真实 simulation 仍执行原始 trace 和有限 buffer，不会将实际流量强制转换为独立到达流。
6. 提供 burst synthetic traffic、真实 workload 和 measured-delay oracle 的验证结果。

下一次投稿不能只声称“采用更一般的 G/G/1”。正文或补充材料应至少给出以下证据链：

```text
真实 AXI transaction trace
  -> measured CA2 shows whether Poisson is valid
  -> G/G/1 reduces burst-related underestimation
  -> queue feedback covers residual correlation/backpressure error
  -> allocation decisions approach measured-delay oracle
  -> performance avoids D2D saturation and baseline regression
```

若真实 workload 的 $C_A^2$ 大多接近 1，也应如实报告。此时论文结论应是 M/G/1 在这些 workload 下经验上可用，而 G/G/1+feedback 为少核、同步访问和 eviction burst 提供鲁棒性，不能人为筛选只支持 burst-aware 模型的窗口。

## 16. 实施优先级

### P0：修正原模型错误

- 服务时间改为 AXI transaction/burst 在瓶颈链路上的实际 serialization or locked-busy time，不再使用 `max(RTT, serialization)`。
- 固定延迟和排队延迟分离。
- 使用 transaction/s 与 transaction service time 的一致单位；若底层以 beat/flit 仲裁，则统一切换到实际仲裁单元，不能混用 transaction arrival rate 与 flit service time。
- utility 全部统一为 cycles/s 或 cycles/access。
- 使用总 queue cost difference 计算拥塞外部性。

### P1：加入 burst-aware moments

- 记录 inter-arrival time。
- 计算并平滑 $C_A^2$。
- 按 AXI channel、transaction type 和 burst-length mixture 计算 $C_S^2$。
- 实现 G/G/1 moment predictor 和 $\rho_{max}$ guard。

### P2：加入 measured queue feedback

- 记录真实 enqueue-to-service delay、queue occupancy 和 backpressure。
- 实现 $\beta$ 校准与硬反馈阈值。
- 支持持续拥塞时收缩 allocation。

### P3：验证与论文材料

- 完成模型/模拟器/oracle 对比。
- 完成 burst traffic 与 writeback stress test。
- 完成策略消融、误差和分配决策一致率分析。
- 更新论文公式、算法、limitations 和 reviewer response。

## 17. 实验启动前检查清单

只有以下项目全部确认后，才启动大规模 workload sweep：

### 17.1 模型语义

- [ ] 明确 AXI4 数据宽度、最大 burst length 和 D2D bridge framing。
- [ ] 明确实验是完整 AXI timing model 还是 AXI-like abstraction，并在论文中使用一致措辞。
- [ ] 明确 R/W burst 是否从 grant 保持到 `RLAST/WLAST`，以及是否允许不同 ID 在 beat/flit 级交错。
- [ ] 明确请求与响应是否共享物理 serializer，或分别建模有向链路。
- [ ] 确认 customer、enqueue、service start、service end 四个观测点与实际仲裁粒度一致。
- [ ] 确认 RTT 只计入固定延迟，不进入 service time。
- [ ] 确认 queue waiting 没有重复计入 $S$ 和 $W_q^{obs}$。

### 17.2 拓扑与单位

- [ ] 解决“每核独立 `SerialLink` 与全局 $\lambda$”的不一致。
- [ ] 确认所有 arrival rate 使用 transaction/s，service time 使用 s 或 simulator ticks，并在公式入口统一单位。
- [ ] 确认带宽使用 decimal GB/s 还是 binary GiB/s，并在论文和 gem5 配置中保持一致。
- [ ] 确认 AR/AW/R/W/B 或 bridge message class 的大小与 burst mapping 可从配置复现。
- [ ] 确认 `ReadReq` 等控制事务没有直接按 `pkt->getSize()` 的 cache-line 大小计费。
- [ ] 确认 writeback、remote miss response 和控制事务没有从流量统计中遗漏。

### 17.3 统计正确性

- [ ] 用人工 trace 验证 $\lambda$、$E[A]$、$C_A^2$。
- [ ] 用固定和混合 burst length 验证 $E[S]$、$E[S^2]$、$C_S^2$。
- [ ] 验证 $C_A^2=1$ 时 G/G/1 与 M/G/1 数值一致。
- [ ] 验证 service start/end 统计得到的 busy time 与链路实际发送周期一致。
- [ ] 验证 observed queue delay 与 queue occupancy、retry/backpressure 变化方向一致。
- [ ] 验证低样本窗口、零等待窗口、$\rho\rightarrow\rho_{max}$ 和数值溢出处理。

### 17.4 分配器正确性

- [ ] 候选 utility 使用 cycles/s 或 cycles/access 的统一量纲。
- [ ] 候选 queue cost 使用 $\Lambda'W_q'-\Lambda W_q$，包含对既有流量的外部性。
- [ ] 0 way → 1 way 的 remote-lookup activation cost 被正确建模。
- [ ] 多 way lookahead 使用 per-way score 或有明确的累计收益解释。
- [ ] utilization、occupancy 和 feedback guard 能拒绝危险候选。
- [ ] 持续拥塞时 allocation 能收缩且不会频繁振荡。

### 17.5 数据与可复现性

- [ ] 每次运行记录 git revision、完整命令行、随机种子和配置 dump。
- [ ] 原始 window-level statistics 与最终汇总结果分开保存。
- [ ] 所有图表可由脚本从原始统计重新生成。
- [ ] M/G/1、G/G/1、feedback 和 oracle 使用完全相同的 workload region 与硬件配置。
- [ ] 失败、超时和不稳定运行不能静默排除，需在实验日志中记录原因。

## 18. 最终论文边界声明

本模型的可辩护结论是：

> UACC uses measured first-two-moment traffic statistics and runtime queue feedback to make its allocation decisions robust to bursty AXI transaction arrivals and D2D backpressure.

本模型不声称：

- 对任意相关到达过程给出精确闭式解。
- 预测 worst-case、P99 或实时上界。
- 替代 cycle-accurate NoC/D2D simulation。
- 与 Mandal 等人的 GGeo/maximum-entropy 网络模型具有相同的建模范围。

如果实验显示该轻量模型在强相关或极端 burst 下仍有明显误判，应将这些配置作为 limitation 报告，并依靠 occupancy/backpressure guard 保证安全，而不是将解析近似描述为完整流量模型。
