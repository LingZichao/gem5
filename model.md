# UACC 突发感知拥塞模型

## 1. 目标与范围

本文档定义 UACC congestion-aware allocator 的最终模型及实验要求，并与 [plan.md](./plan.md) 的 classic timing 实现保持一致。

模型用于回答：增加远端 cache way 后，容量收益是否大于新增 D2D 拥塞代价。

本轮范围包括：

- gem5 classic timing mode；
- `UACCController` profiling window；
- ATD/reuse-distance 预测；
- `SerialLink` 和共享 `NoncoherentXBar`；
- packet 级到达、服务和实际排队统计；
- G/G/1 moment approximation 与运行时反馈；
- 合成流量和真实 workload 验证。

本轮不包括：

- AXI 协议或 channel-level 建模；
- 完整 GGeo/maximum-entropy queueing network；
- Ruby/Garnet flit-level mesh；
- worst-case 或 tail-latency 理论保证；
- 对真实 D2D PHY 的 cycle-accurate 复现。

## 2. 最终选择

UACC 使用：

> **G/G/1 moment approximation + measured queue feedback**

G/G/1 根据到达间隔和服务时间的前两阶矩预测候选 allocation 的平均排队延迟；实测 feedback 用于校准突发相关性、有限 buffer、仲裁和 backpressure 等解析模型未覆盖的影响。

该模型是原 M/G/1 的自然扩展。当到达过程接近 Poisson，即 $C_A^2=1$ 时，G/G/1 公式退化为 M/G/1。

模型只用于 allocation ranking 和 saturation avoidance，不声称精确预测任意突发流量的完整延迟分布。

## 3. 排队模型

### 3.1 建模对象

每个实际发生竞争的有向链路或共享端口对应一个 queueing domain。customer 是进入该队列的 gem5 timing packet。

必须遵守：

- 独立 `SerialLink` 使用各自的到达率和统计量；
- 只有共享同一服务资源的流量才能合并为一个全局到达率；
- 请求与响应方向若具有独立队列，必须分别建模；
- 共享 `NoncoherentXBar` 的竞争不能错误地归入某个独立 `SerialLink`。

### 3.2 固定延迟与服务时间

远端访问延迟分解为：

$$
L_{remote}=L_{fixed}+S+W_q.
$$

其中：

- $L_{fixed}$：传播、路由、cache lookup 等不持续占用瓶颈资源的延迟；
- $S$：packet 占用服务资源的时间；
- $W_q$：进入队列后等待获得服务的时间。

RTT 属于固定延迟，不能作为服务时间。对 packet $n$：

$$
S_n=\frac{B_n}{BW},
$$

其中 $B_n$ 是该 packet 在 `SerialLink` 模型中的传输字节数，$BW$ 是 payload bandwidth。

请求、数据响应、miss response 和 writeback 可以具有不同的 $B_n$，其差异通过一般服务时间分布 $G$ 表达。

### 3.3 到达过程

连续 packet 的入队时间为 $t_n$，到达间隔为：

$$
A_n=t_n-t_{n-1}.
$$

到达间隔平方变异系数为：

$$
C_A^2=\frac{\operatorname{Var}(A)}{E[A]^2}.
$$

- $C_A^2\approx1$：接近 Poisson；
- $C_A^2>1$：存在 burstiness，M/G/1 可能低估排队延迟；
- $C_A^2<1$：到达过程比 Poisson 更规则。

少核、streaming、MSHR 批量完成和 dirty eviction 都可能导致 $C_A^2>1$。

### 3.4 服务时间矩

profiling window 内有 $N$ 个 packet：

$$
E[S]=\frac{1}{N}\sum_{n=1}^{N}S_n,
$$

$$
E[S^2]=\frac{1}{N}\sum_{n=1}^{N}S_n^2,
$$

$$
C_S^2=\frac{E[S^2]-E[S]^2}{E[S]^2}.
$$

服务时间只包含资源实际 busy 的时间，不包含 enqueue-to-service waiting time，避免重复计算拥塞。

### 3.5 G/G/1 moment approximation

设到达率为：

$$
\lambda=\frac{N}{T},
$$

利用率为：

$$
\rho=\lambda E[S].
$$

平均排队等待时间采用 Kingman approximation：

$$
W_q^{model}
\approx
\frac{\rho}{1-\rho}
\frac{C_A^2+C_S^2}{2}E[S].
$$

当 $C_A^2=1$ 时：

$$
W_q^{model}
=
\frac{\rho}{1-\rho}
\frac{1+C_S^2}{2}E[S],
$$

与 M/G/1 的 Pollaczek-Khinchine 结果一致。

## 4. Profiling-window 统计

每个 queueing domain 维护：

```text
arrival_count
last_arrival_tick
interarrival_mean
interarrival_M2
service_mean
service_M2
observed_wait_sum
observed_wait_samples
queue_occupancy_sum
queue_occupancy_max
buffer_full_events
backpressure_events
```

方差使用 Welford 算法或等价的数值稳定方法计算。

窗口间使用 EWMA：

$$
\bar{x}_t=\alpha x_t+(1-\alpha)\bar{x}_{t-1}.
$$

首轮默认参数：

```text
profile_interval = 100000 cycles
min_arrival_samples = 32
moment_ewma_alpha = 0.2
feedback_ewma_alpha = 0.2
rho_max = 0.90
feedback_beta_max = 4.0
```

样本不足时沿用上一窗口结果。启动时设置 $C_A^2=1$、feedback factor 为 1。

为避免低估突发风险，默认使用：

$$
C_{A,eff}^2=\max(1,\operatorname{EWMA}(C_A^2)).
$$

是否允许 $C_A^2<1$ 降低拥塞预测作为敏感性实验评估。

## 5. Measured queue feedback

实际平均等待时间为：

$$
W_q^{obs}
=
\frac{1}{N}\sum_{n=1}^{N}
(t_{service,n}-t_{arrival,n}).
$$

定义校准因子：

$$
\beta
=
\operatorname{clip}
\left(
\frac{W_q^{obs}}
     {\max(W_q^{model,current},\epsilon)},
1,\beta_{max}
\right).
$$

对 $\beta$ 使用 EWMA。候选 allocation 的预测为：

$$
\widehat W_q(\mathbf{k}')
=
\bar{\beta}W_q^{model}(\mathbf{k}').
$$

实测等待时间只反映当前 allocation，不能直接替代候选预测；$\beta$ 用于校准解析模型的系统性低估。

满足以下任一条件时拒绝扩容候选：

- 任一相关 queueing domain 的 $\rho(\mathbf{k}')\geq\rho_{max}$；
- queue occupancy 超过标定阈值；
- buffer-full 或 backpressure events 超过标定阈值；
- 模型输入无效或发生数值溢出。

若实际拥塞持续多个窗口，allocator 应允许收缩 allocation。

## 6. 候选 allocation 成本

### 6.1 ATD 容量收益

设核心 $i$ 的本地 miss rate 为：

$$
m_i=\frac{N_{miss,i}}{T}.
$$

ATD 预测分配 $k_i$ 个远端 way 时的远端命中概率：

$$
p_i(k_i)
=
\frac{
\sum_{r=Pos_{base}}^{Pos_{base}+k_i-1}H_i[r]
}{N_{miss,i}}.
$$

候选 $k_i\rightarrow k_i+\Delta$ 的新增远端命中率为：

$$
\Delta h_i
=
m_i[p_i(k_i+\Delta)-p_i(k_i)].
$$

若避免一次 lower-level miss 节省 $L_{lower,i}$ cycles：

$$
G_i=\Delta h_iL_{lower,i}.
$$

$G_i$ 的单位为 cycles/s。

### 6.2 候选流量

候选 allocation 根据 ATD 预测的 remote lookup、hit、miss 和 writeback 变化，生成每个 queueing domain 的候选 packet rates：

$$
\lambda'_{\ell,j}
=
\lambda_{\ell,j}+\Delta\lambda_{\ell,j}.
$$

其中 $\ell$ 表示 queueing domain，$j$ 表示 packet class。

从 0 way 增加到 1 way 时，所有符合条件的本地 miss 都可能开始产生 remote lookup，因此必须单独计入 activation cost。后续增加 way 主要改变 remote hit/miss response 比例。

### 6.3 固定成本与排队外部性

固定通信成本为：

$$
C_{fixed}(\mathbf{k})
=
\sum_{\ell,j}
\lambda_{\ell,j}(\mathbf{k})L_{fixed,\ell,j}.
$$

queueing domain $\ell$ 的总排队成本为：

$$
C_{q,\ell}(\mathbf{k})
=
f_{cpu}\Lambda_\ell(\mathbf{k})
\widehat W_{q,\ell}(\mathbf{k}),
$$

其中：

$$
\Lambda_\ell=\sum_j\lambda_{\ell,j}.
$$

候选的拥塞外部性为：

$$
\Delta C_q
=
\sum_\ell
\left[
C_{q,\ell}(\mathbf{k}')-C_{q,\ell}(\mathbf{k})
\right].
$$

使用 $\Lambda W_q$ 的总成本差，才能计入新增流量对既有请求造成的延迟，而不是只计算单个请求的等待时间差。

### 6.4 最终效用

$$
MU_i(\Delta)
=
G_i
-[C_{fixed}(\mathbf{k}')-C_{fixed}(\mathbf{k})]
-\Delta C_q.
$$

所有项统一为 cycles/s。只有 $MU_i(\Delta)>0$ 且通过所有拥塞 guard 时，候选才可接受。

若评估 $\Delta>1$ 但每次只提交一个 way，候选排序使用：

$$
Score_i(\Delta)=\frac{MU_i(\Delta)}{\Delta}.
$$

## 7. 分配算法

```text
collect ATD and per-domain queue statistics
update lambda, CA2, CS2 and feedback beta

initialize candidate allocation vector k

while remote ways remain:
    best_candidate = none

    for each core i:
        for each lookahead delta:
            predict candidate packet rates

            if utilization or measured-congestion guard fails:
                reject candidate
                continue

            compute capacity gain
            compute fixed-cost difference
            compute total queue-cost difference
            compute utility and per-way score

            retain highest positive score

    if no positive candidate exists:
        break

    commit one way to the selected core
    update predicted traffic

apply allocation

if measured congestion remains high for multiple windows:
    contract the lowest-benefit allocation
```

## 8. gem5 修改要求

### 8.1 `SerialLink` 或等价队列

需要暴露或记录：

- packet enqueue tick；
- service-start tick；
- service busy time；
- queue occupancy；
- buffer-full 和 retry/backpressure events。

观测必须位于真实排队点。只在 `UACCController` 接收到逻辑访问事件时记录，无法得到真实等待时间。

### 8.2 `UACCController`

每个 queueing domain 保存：

```text
lambda
CA2_raw / CA2_ewma
ES / ES2 / CS2
rho
Wq_model / Wq_observed
feedback_beta
queue occupancy and backpressure counters
```

新增建议参数：

```text
min_arrival_samples
moment_ewma_alpha
feedback_ewma_alpha
feedback_beta_max
rho_max
queue_occupancy_threshold
backpressure_threshold
contraction_windows
```

### 8.3 拓扑一致性

`plan.md` 当前为每核一个 `SerialLink`，因此不能将所有核心流量直接合并后套用单条链路的服务时间。

首版采用：

- 每核 `SerialLink`：独立统计和预测；
- 共享 `UACCXBar`：使用实测 occupancy/backpressure 表示共享竞争；
- 若后续改为共享 D2D link，再将通过该 link 的流量合并为一个 queueing domain。

解析模型与模拟拓扑必须使用相同的共享关系。

## 9. 验证方案

### 9.1 单元测试

- $C_A^2=1$ 时 G/G/1 与 M/G/1 一致；
- 固定服务时间时 $C_S^2=0$；
- $W_q$ 随 $C_A^2$、$C_S^2$ 和 $\rho$ 单调增加；
- $\rho\geq\rho_{max}$ 时拒绝候选；
- $\beta$ 保持在 $[1,\beta_{max}]$；
- 多 packet size 的 $E[S]$、$E[S^2]$ 和 $C_S^2$ 正确；
- 总 queue cost 包含对既有流量的外部性；
- 样本不足、零等待和数值溢出路径正确。

### 9.2 合成流量

至少覆盖：

- Poisson/geometric arrivals；
- deterministic arrivals；
- on/off burst traffic；
- sequential cache-line streaming；
- 多核同步 memory phase；
- MSHR completion burst；
- dirty writeback burst。

扫描：

```text
arrival rate / utilization
burst length and on/off duration
packet-size mixture
D2D bandwidth
buffer depth
core count
```

### 9.3 对比策略

```text
distance-only
original RTT-based M/G/1
serialization-based M/G/1
G/G/1 moments
G/G/1 moments + feedback
direct queue-cost
direct queue-cost + feedback
measured-delay oracle
```

### 9.4 报告指标

- $C_A^2$、$C_S^2$ 和利用率分布；
- mean/median prediction error；
- P95 和最大 underestimation；
- allocation decision agreement with oracle；
- buffer-full/retry/backpressure events；
- IPC/speedup；
- 相对 monolithic baseline 是否发生 regression；
- 状态存储和每窗口计算开销。

对 $W_q^{obs}>0$ 的窗口：

$$
e_t=\frac{W_{q,t}^{pred}-W_{q,t}^{obs}}
          {W_{q,t}^{obs}},
$$

$$
u_t=\max(0,-e_t).
$$

$u_t$ 表示低估程度。低等待时间窗口同时报告绝对 cycle error。

### 9.5 实验顺序

1. 单链路/单队列校准统计和公式；
2. 多核 burst、MSHR 和 writeback 压力测试；
3. 真实 workload 与 LP/BP/HP mixes；
4. profiling window、EWMA、$\rho_{max}$ 和 $\beta_{max}$ 敏感性；
5. 模型、feedback 和 oracle 消融对比。

实验启动前必须先解决拓扑统计归属和真实排队观测点，否则后续结果没有解释力。

## 10. 投稿前判断标准

新模型应至少证明：

- 在 burst 和高利用率窗口中，G/G/1+feedback 的低估显著小于 M/G/1；
- allocation 决策更接近 measured-delay oracle；
- buffer-full/backpressure 和错误扩容减少；
- 最终性能不会因模型低估持续跌破 monolithic baseline；
- 新增状态和计算开销与在线 allocator 的定位相符。

若 G/G/1 只改善公式误差，却不改善 allocation、拥塞或性能，则不能将其作为主要贡献。若真实流量的 $C_A^2$ 普遍接近 1，应如实报告 M/G/1 在这些配置下经验上有效，并将 G/G/1+feedback 定位为针对少核和突发阶段的鲁棒扩展。

## 11. 论文表述

推荐表述：

> UACC uses a lightweight G/G/1 moment approximation to estimate the congestion impact of candidate capacity allocations. The predictor measures the aggregate packet arrival rate, inter-arrival-time variation, and service-time moments at each modeled D2D queue. Since second-order statistics cannot fully capture temporally correlated bursts, finite buffering, and backpressure, UACC calibrates the estimate using measured queueing delay and applies utilization and queue-occupancy guardrails. The model is used for allocation ranking and saturation avoidance rather than exact latency-distribution prediction.

与 GGeo 工作的关系：

> Prior burst-aware NoC models use generalized-geometric traffic and maximum-entropy decomposition to propagate burst statistics through complex priority and deflection networks. UACC adopts the lightweight first-two-moment characterization of burstiness, but does not reconstruct a complete NoC queueing network because its predictor executes online and targets a small number of D2D bottlenecks.

需要避免：

- “G/G/1 accurately models arbitrary correlated traffic.”
- “The predictor captures all temporal correlations.”
- “RTT is the link service time.”
- “The analytical model replaces timing simulation.”
- “Mandal et al. use a standard G/G/1 model.”

## 12. 审稿意见回应要点

针对 Poisson 假设可能低估 burst delay 的质疑：

1. 承认裸 M/G/1 在 $C_A^2>1$ 时可能低估排队延迟；
2. 使用实测 inter-arrival moments，而不是固定 Poisson 假设；
3. 说明 $C_A^2=1$ 时新模型退化为原 M/G/1；
4. 使用实际 queue delay、occupancy 和 backpressure 校准未建模相关性；
5. 在真实 trace、有限 buffer 和 synthetic burst 下验证 underestimation；
6. 与 measured-delay oracle 比较 allocation 和性能，而不仅比较公式误差。

这套证据能够直接回应原审稿意见，但不能保证审稿人不提出新的模型范围、反馈滞后或实现开销问题，因此实验必须同时报告敏感性和开销。

## 13. 实施顺序

### P0：修正原模型

- RTT 与服务时间分离；
- 服务时间改为实际 serialization/busy time；
- 统一 rate、time 和 utility 单位；
- 按真实共享资源拆分 queueing domain；
- 使用总 queue-cost difference。

### P1：加入 moments

- 记录 inter-arrival 和 service time；
- 计算 $C_A^2$、$C_S^2$；
- 实现 G/G/1 predictor 和利用率 guard。

### P2：加入 feedback

- 记录实际 queue wait、occupancy 和 backpressure；
- 实现 $\beta$ 校准；
- 支持持续拥塞时收缩 allocation。

### P3：实验与论文

- 完成 synthetic calibration；
- 完成多核 burst/writeback stress；
- 完成真实 workload 和 oracle 对比；
- 更新论文公式、算法、limitations 和 rebuttal。

## 14. 工程实现最终公式

本节是 UACC allocator 的最终工程版本。候选计算全部使用每个 profiling window 的 packet count，不再显式计算候选到达率、平均服务时间、服务时间方差或平均等待时间。

所有成本统一为：

~~~text
cycles / profiling window
~~~

### 14.1 硬件状态

对每个实际 queueing domain，窗口长度为 $T$ cycles。运行时维护：

| 状态 | 含义 |
|---|---|
| $N$ | 当前窗口 packet 数 |
| $M$ | 有效 inter-arrival sample 数 |
| $S_A=\sum A_n$ | inter-arrival time 累加 |
| $S_{A2}=\sum A_n^2$ | inter-arrival square 累加 |
| $n_j$ | 第 $j$ 类 packet 的数量 |
| $S_W=\sum W_{q,n}^{obs}$ | 实测 queue-wait cycles 总和 |
| buffer-full counter | buffer-full 次数 |
| backpressure counter | retry/backpressure 次数 |

每类 packet 的服务时间 $S_j$ 和 $S_j^2$ 是配置常数。实现只累计 packet-class count，不需要逐 packet 计算 service-time square。

### 14.2 Burstiness

当前窗口的到达间隔平方变异系数直接由整数和计算：

$$
C_A^2
=
\max\left(
0,
\frac{M S_{A2}}{S_A^2}-1
\right).
$$

边界处理：

- 若 $M=0$，沿用上一窗口结果；
- 若 $M>0$ 且 $S_A=0$，设置为 CA2_MAX；
- 所有结果在写回前饱和到配置位宽。

使用 shift-EWMA：

$$
\bar C_A^2
\leftarrow
\bar C_A^2
+
\frac{C_A^2-\bar C_A^2}{2^{g_A}}.
$$

最终使用：

$$
C_{A,eff}^2=\max(1,\bar C_A^2).
$$

建议首版使用 $g_A=2$，即 $\alpha_A=1/4$。

### 14.3 候选 packet count

ATD 对候选 allocation 直接预测窗口内 packet 数，而不是先计算 probability 或 packet rate。

对核心 $i$：

$$
\Delta H_i
=
H_i(k_i+\Delta)-H_i(k_i),
$$

其中 $H_i(k)$ 是 ATD 预测在 $k$ 个远端 way 下的远端命中数量。

典型 packet class count 为：

~~~text
request_count       = local_miss_count, if allocation > 0
hit_response_count  = predicted_remote_hits
miss_response_count = local_miss_count - predicted_remote_hits
writeback_count     = measured or separately predicted writebacks
~~~

从 0 way 增加到 1 way 时，必须计入 remote-lookup activation cost。

### 14.4 直接 queue-cost

对候选 allocation $\mathbf{k}$，定义：

$$
R_0(\mathbf{k})
=
\sum_j n_j(\mathbf{k}),
$$

$$
R_1(\mathbf{k})
=
\sum_j n_j(\mathbf{k})S_j,
$$

$$
R_2(\mathbf{k})
=
\sum_j n_j(\mathbf{k})S_j^2.
$$

含义：

- $R_0$：窗口内 packet 总数；
- $R_1$：窗口内预测 busy cycles；
- $R_2$：服务时间二阶加权和。

利用率 guard 不需要除法：

$$
R_1(\mathbf{k})<\rho_{max}T.
$$

若不满足，直接拒绝候选。

G/G/1 的总排队成本直接计算为：

$$
\boxed{
Q^{model}(\mathbf{k})
=
\frac{
R_1(\mathbf{k})^2
\left(C_{A,eff}^2-1\right)
+
R_0(\mathbf{k})R_2(\mathbf{k})
}{
2\left(T-R_1(\mathbf{k})\right)
}
}
$$

该式的输出单位为 queue stall cycles/window，与先计算候选 $E[S]$、$C_S^2$、$W_q$，再乘 packet rate 的完整 G/G/1 路径代数等价。

候选数据通路为：

~~~text
busy_square = R1 * R1
burst_term  = busy_square * (CA2_eff - 1)
service_term = R0 * R2
numerator   = burst_term + service_term
slack       = T - R1
queue_cost  = numerator / (2 * slack)
~~~

每个候选只保留一次 reciprocal/division。

### 14.5 Feedback

当前窗口的实测总排队成本为：

$$
Q^{obs}=S_W.
$$

feedback factor 为：

$$
\beta_{raw}
=
\operatorname{clip}
\left(
\frac{Q^{obs}}
     {\max(Q^{model,current},\epsilon)},
1,\beta_{max}
\right).
$$

使用 shift-EWMA：

$$
\bar\beta
\leftarrow
\bar\beta+
\frac{\beta_{raw}-\bar\beta}{2^{g_\beta}}.
$$

建议首版使用 $g_\beta=2$、$\beta_{max}=4$。

候选最终排队成本：

$$
\widehat Q(\mathbf{k})
=
\bar\beta Q^{model}(\mathbf{k}).
$$

feedback 只使用已经完成的当前窗口数据，不能读取候选或下一窗口 trace。

### 14.6 Utility

容量收益：

$$
G_i(\Delta)
=
\Delta H_i L_{lower,i}.
$$

固定通信成本：

$$
F(\mathbf{k})
=
\sum_{\ell,j}
n_{\ell,j}(\mathbf{k})L_{fixed,\ell,j}.
$$

最终边际效用：

$$
\boxed{
MU_i(\Delta)
=
G_i(\Delta)
-
\left[
F(\mathbf{k}')-F(\mathbf{k})
\right]
-
\sum_\ell
\left[
\widehat Q_\ell(\mathbf{k}')
-
\widehat Q_\ell(\mathbf{k})
\right]
}
$$

接受候选必须同时满足：

~~~text
MU_i(delta) > 0
R1_link < rho_max * T
queue occupancy guard passes
backpressure guard passes
~~~

若 lookahead $\Delta\in\{1,2,4\}$ 但每次只提交一个 way：

$$
Score_i(\Delta)=\frac{MU_i(\Delta)}{\Delta}.
$$

除以 $\Delta$ 分别实现为不移位、右移 1 位和右移 2 位。

### 14.7 HLS/RTL 执行顺序

~~~text
per packet:
    update arrival sums, packet-class count, and observed wait sum

at window boundary:
    1. compute CA2 and update shift-EWMA
    2. update feedback beta from the completed window
    3. build candidate packet counts from ATD
    4. accumulate candidate R0, R1, and R2
    5. reject utilization/backpressure violations
    6. compute direct queue cost
    7. compute fixed cost, capacity gain, and utility
    8. retain the highest positive score
    9. commit allocation and reset window counters
~~~

实现建议：

- $S_j$、$S_j^2$ 和 $L_{fixed,j}$ 预存为常数；
- 所有候选共享乘法器和 reciprocal/divider；
- EWMA 和 lookahead division 使用移位；
- 累加器使用整数饱和运算；
- $C_A^2$、$\beta$ 和 reciprocal 使用参数化定点格式；
- 计算不进入 packet data-path critical path。

### 14.8 已完成验证

实现位置：

- util/uacc_gg1_sim.py：rate-direct 和 window-count direct；
- util/compare_uacc_models.py：窗口级、多 seed allocation equivalence；
- tests/pyunit/uacc/pyunit_gg1_sim.py：公式与因果 allocation 单测。

验证结果：

~~~text
13 Python tests passed
py_compile passed
pycodestyle passed
git diff --check passed

20-seed causal allocation:
full G/G/1 vs window-count agreement = 100%
window-count vs measured oracle agreement = 100%
max window-count candidate objective difference = 2.665e-15
mean window-count oracle regret = 0
~~~

窗口级 window-count 与 rate-direct 的最大差异为：

$$
4.263\times10^{-14}.
$$

完整浮点 G/G/1 与 raw-sum/direct 路径的最大差异为：

$$
1.563\times10^{-13}.
$$

因此，window-count direct 公式是最终工程实现路径。在理想算术下没有观察到 prediction、objective 或 allocation 损失。尚未量化的误差只包括 Q-format、取整、饱和位宽和 reciprocal approximation。
