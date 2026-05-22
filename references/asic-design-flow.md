# ASIC 设计流程参考

## 概述

ASIC 流程将 RTL 转化为可制造的 GDSII 版图。本参考覆盖两条路径：

- **商业工具链**：Synopsys (DC/ICC2/PrimeTime) / Cadence (Genus/Innovus/Tempus)
- **开源工具链**：Yosys + OpenROAD + OpenSTA + Magic/netgen，目标工艺 ICSprout55-PDK (55nm)

主要阶段：

1. **RTL 编写与验证**
2. **逻辑综合**（RTL → 门级网表）→ 开源: Yosys，商业: DC/Genus
3. **DFT 插入**（扫描链、BIST、JTAG）
4. **Floorplan 与布局** → 开源: OpenROAD，商业: ICC2/Innovus
5. **时钟树综合（CTS）**
6. **布线** → 开源: OpenROAD，商业: ICC2/Innovus
7. **静态时序分析（STA）** → 开源: OpenSTA，商业: PrimeTime/Tempus
8. **物理验证**（DRC、LVS、ERC）→ 开源: Magic/netgen，商业: Calibre
9. **Sign-off 与 Tape-out**

### 开源工具链快速入口

| 任务 | 工具 | 详细参考 |
|------|------|---------|
| 综合 | Yosys + ICSprout55 | `references/yosys-synthesis.md` |
| 物理设计 | OpenROAD | `references/physical-design.md` |
| 电路评估 | `scripts/eval_circuit.py` | 自动生成 QoR 报告 |
| 一键综合 | `scripts/yosys_synth.py` | 自动生成 Yosys 脚本并执行 |

## 1. 逻辑综合

### 目标
将 RTL 映射到目标工艺库标准单元，同时满足时序、面积和功耗约束。

### 核心概念
- **约束**：SDC（Synopsys Design Constraints）定义时钟、IO 延迟、false path、multicycle path
- **优化**：compile/compile_ultra 迭代、边界优化、retiming
- **输出**：Verilog 网表、SDC、SPEF（寄生参数）、时序报告

### 常用命令（Design Compiler / Genus）
```tcl
# 读入设计
read_verilog my_design.v
read_sdc constraints.sdc

# 链接并编译
link
current_design MyTop
check_design
compile_ultra -gate_clock -retime

# 报告
report_timing
report_area
report_power
report_qor

# 写出结果
write -format verilog -hierarchy -output netlist.v
write_sdc out.sdc
```

### 常用命令（Yosys + ICSprout55-PDK）

```tcl
# 读入设计
read_verilog my_design.v
hierarchy -check -top MyTop

# 工艺无关优化
proc; flatten; opt_expr; opt_clean
fsm; opt
wreduce; share; peepopt; opt_clean

# 工艺映射 (ICSprout55)
read_liberty -lib /path/to/icsprout55-pdk/libs.ref/icsprout55_stdcell.lib
dfflibmap -liberty /path/to/icsprout55-pdk/libs.ref/icsprout55_stdcell.lib
abc -liberty /path/to/icsprout55-pdk/libs.ref/icsprout55_stdcell.lib

# 映射后
opt_clean
stat -liberty /path/to/icsprout55-pdk/libs.ref/icsprout55_stdcell.lib

# 输出
write_verilog -noexpr netlist.v
```

或使用一键脚本：
```bash
python scripts/yosys_synth.py my_design.v --top MyTop --pdk-path /path/to/icsprout55-pdk
python scripts/eval_circuit.py synth_output --target-freq-mhz 100
```

### 综合调试
- **未映射单元**：检查库链接、黑盒模块
- **大面积/时序违例**：审视层级展平、分组和约束的松紧度
- **锁存器推断**：运行 `check_design` 或 lint；修复不完整的 case/if 赋值

### 最小 SDC 入门
将 SDC 视为可执行的时序规格。保持时钟、生成时钟、IO 延迟、时钟不确定度和例外明确。

```tcl
create_clock -name core_clk -period 10.000 [get_ports clk]
set_clock_uncertainty 0.100 [get_clocks core_clk]
set_input_delay  1.000 -clock core_clk [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay 1.000 -clock core_clk [all_outputs]

# 仅在路径架构上豁免时使用。
set_false_path -from [get_clocks async_clk_a] -to [get_clocks async_clk_b]
set_multicycle_path 2 -setup -from [get_pins u_src_reg*/Q] -to [get_pins u_dst_reg*/D]
set_multicycle_path 1 -hold  -from [get_pins u_src_reg*/Q] -to [get_pins u_dst_reg*/D]
```

综合前检查清单：
- [ ] 每个功能时钟都有 `create_clock` 或 `create_generated_clock`
- [ ] IO 延迟绑定到真实的 launch/capture 时钟
- [ ] False path 有同步器、复位、扫描/测试模式或静态配置等合理依据
- [ ] Multicycle setup 路径有对应的 hold 调整
- [ ] 异步复位路径已约束 recovery/removal，或按流程策略有意豁免

## 2. DFT 插入

### 目标
确保制造后的可测试性。

### 技术
| 技术 | 用途 | 工具 |
|-----------|---------|------|
| Full Scan | 时序 ATPG | DFT Compiler、Tessent |
| Boundary Scan (JTAG) | 板级测试 | BSD Compiler |
| MBIST | 存储器自测试 | Tessent、Mentor |
| LBIST | 逻辑自测试 | Tessent |

### 关键检查
- 扫描链均衡（最小化偏移）
- 测试覆盖目标（>98% stuck-at 故障覆盖率为典型值）
- 扫描模式下无未经处理的时钟域交叉

### 扫描友好的 RTL
- 除非 DFT 方法论明确允许，否则避免推断锁存器
- 通过库 ICG 单元（含 scan enable/test enable 引脚）实现时钟门控
- 确保触发器在测试模式下可控；避免不含测试旁路的内部生成时钟
- 记录无复位触发器和存储器，使 ATPG/X 传播处理成为有意识的设计决策

## CDC/RDC 审查

CDC（时钟域交叉）和 RDC（复位域交叉）问题除非专门强调，通常仿真中会漏过。

常用安全模式：
- 单比特电平信号：目标时钟域中的 2 级触发器同步器
- 单周期脉冲：脉冲-翻转同步器或 req/ack 握手
- 多比特数据：异步 FIFO、Gray 码指针或数据稳定下的 data-valid 握手
- 异步复位释放：每个时钟域配备复位同步器

审查清单：
- [ ] 每条跨域路径的源时钟、目的时钟和协议已记录
- [ ] 源触发器与第一级同步触发器之间无组合逻辑
- [ ] 同步后的单比特控制信号不复用扇出到无关的重汇聚逻辑
- [ ] 多比特总线不逐位同步（除非 Gray 码或 one-hot 并有证明）
- [ ] 复位释放与各接收时钟同步
- [ ] 扫描/测试模式不会不安全地旁路同步器

## 3. Floorplan 与布局

### Floorplan
- 定义芯片面积、核心面积、IO Pad 环
- 放置硬宏（存储器、PLL、模拟模块）
- 创建电源环和电源条（PG 网络）
- 设置布线轨道、利用率目标（通常 70-80%）

### 布局
- 初始全局布局（最小化线长）
- 详细布局（对齐到标准单元行）
- 优化：缓冲、门尺寸调整、单元分布

### 关键检查
- 拥塞图（理想溢出 < 2%）
- 布局后时序（乐观 RC 下 setup slack > 0）
- 电源网格 IR Drop（EM/SM 检查）

## 4. 时钟树综合（CTS）

### 目标
为所有时序单元提供低偏斜时钟。

### 概念
- **Skew（偏斜）**：时钟到达时间的最大差异
- **插入延迟（Insertion delay）**：时钟从根到叶的延迟
- **有用偏斜（Useful skew）**：为优化时序引入的有意偏斜

### 最佳实践
- 数据路径优化之前先均衡时钟树
- 时钟网络加屏蔽以减少 SI（信号完整性）问题
- 不同时钟域使用独立时钟树

### 时钟门控
绝不使用原始 RTL 如 `assign gclk = clk & en;` 做 ASIC 实现的门控。应使用综合工具插入的集成门控时钟单元，或通过流程批准的封装显式实例化。

综合工具通常能识别的 RTL 模式：
```systemverilog
always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) q <= '0;
    else if (en) q <= d;
end
```

时钟门控检查：
- [ ] 使能在时钟有效沿之前稳定
- [ ] ICG 单元在需要处接收 scan/test enable
- [ ] 生成/门控时钟对 STA 和 CTS 可见
- [ ] 门控不产生隐藏的 CDC 或复位顺序问题

## 5. 布线

### 阶段
1. **全局布线**：在粗网格上进行布线规划
2. **轨道分配**：将网络分配到具体轨道
3. **详细布线**：实际金属层分配和几何形状
4. **搜索与修复**：修复 DRC 违例

### 关键检查
- DRC 干净（间距、宽度、凹口、面积规则）
- 无天线违例（或添加二极管单元）
- 提取 RC 后的时序（SPEF）

## 6. 静态时序分析（STA）

### 目标
验证设计满足时序，无需运行完整仿真。

### 概念
- **Setup 检查**：数据在时钟沿之前到达（最大延迟分析，慢 corner）
- **Hold 检查**：数据在时钟沿之后保持稳定（最小延迟分析，快 corner）
- **Corner**：SS（慢-慢）、FF（快-快）、TT（典型），配合温度/电压变化
- **OCV/POCV**：片上变异建模

### 时序调试
- **Setup 违例**：减少数据路径延迟（优化逻辑、插入缓冲、加大单元尺寸）或增加时钟周期
- **Hold 违例**：在数据路径中添加延迟缓冲，或减小时钟偏斜
- **时钟门控检查**：确保使能在时钟沿之前到达

### 时序报告排查
调试时序报告时，按以下顺序检查：

1. **路径类型**：setup、hold、recovery/removal、最小脉冲宽度、时钟门控
2. **约束合理性**：正确时钟周期、生成时钟源、不确定度、IO 延迟和例外
3. **Launch/capture 时钟**：同域、相关生成时钟，或真正的异步跨域
4. **数据路径**：逻辑深度、高扇出网络、长走线网络、慢速单元、缓冲不佳
5. **时钟路径**：偏斜、延迟、CPPR、有用偏斜、CTS 均衡
6. **Corner/模式**：慢 setup corner vs 快 hold corner、功能模式 vs 扫描/测试模式

典型修复：
- Setup：流水线、减少逻辑级数、重构 Mux、加大单元尺寸、缓冲高扇出、放宽过紧约束
- Hold：添加延迟缓冲、调整 CTS 偏斜、修复最小延迟例外、避免启动路径过度缓冲
- Recovery/Removal：同步复位释放并显式约束复位路径
- 时钟门控：提前寄存使能并使用库 ICG 检查

### ECO（工程变更）
- 仅金属层 ECO：仅更改金属层（成本较低）
- 基础层 ECO：需要新掩模（成本高）
- 使用 Conformal/Formality 进行逻辑等价性检查（LEC）

## 7. 物理验证

| 检查 | 用途 | 工具 |
|-------|---------|------|
| DRC | 设计规则检查（可制造性） | Calibre、IC Validator |
| LVS | 版图与电路图等效 | Calibre、IC Validator |
| ERC | 电气规则检查（浮空网络、短路） | Calibre |
| ANT | 天线检查（栅氧损坏） | 同 DRC |
| DFM | 可制造性设计 | Calibre、IC Validator |

## 8. 功耗分析

### 动态功耗
P = α · C · V² · f（翻转率 × 电容 × 电压² × 频率）
- 降低翻转：时钟门控、操作数隔离
- 降低电压：多电压域、DVFS
- 降低频率：架构流水线

### 漏电功耗
- 亚阈值漏电在先进节点中占主导
- 非关键路径使用 HVT 单元，电源门控（关断域）

### 低功耗设计意图
对于多电压或电源门控设计，使用 UPF/CPF 捕获设计意图而非注释。

检查项：
- [ ] 离开关断域的信号配备隔离单元
- [ ] 不同电压域之间配备电平转换器
- [ ] 需要跨电源门控保持状态使用保持触发器
- [ ] 进入可关断域的控制/复位信号使用常开缓冲器
- [ ] 电源状态表覆盖所有合法工作模式

## 快速排查表

| 症状 | 可能原因 | 修复方法 |
|---------|-------------|-----|
| 布线后 Setup 违例 | 数据路径过长，RC 较大 | 加大驱动、插入缓冲、VT 互换 |
| Hold 违例 | 时钟偏斜，数据路径过短 | 添加延迟缓冲、均衡时钟树 |
| 严重拥塞 | 利用率过高，floorplan 不佳 | 分散宏、增加核心面积、重新 floorplan |
| 漏电功耗高 | LVT 单元过多 | 非关键路径换用 HVT |
| 布线后 DRC 违例 | 天线、间距、过孔问题 | 修复布线、添加跳线/二极管 |
| 扫描覆盖率低 | 无时钟/不可控逻辑 | 添加测试点、审查测试模式下的时钟门控 |
| CDC waiver 被拒 | 缺少协议证明或重汇聚风险 | 添加同步器/握手证据和重汇聚分析 |
| 复位 Recovery 违例 | 异步复位释放靠近时钟沿 | 每时钟域添加复位同步器 |
| 生成时钟时序错误 | 缺少或错误的 `create_generated_clock` | 定义源引脚、分频/倍频比例和波形 |
