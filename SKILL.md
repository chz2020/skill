---
name: riscv-design
description: "RISC-V 处理器 RTL 设计、验证、Yosys 逻辑综合、电路评估、物理设计 (RTL→GDSII)。基于 ICSprout55-PDK (55nm) 开源工艺。当用户处理具体的 RISC-V RTL 或验证任务时使用：编写/修改 .v/.sv 模块、审查可综合逻辑、生成 testbench、修复 lint/综合/时序/CDC/复位/DFT 问题，或进行 Yosys 综合与物理设计。不适用于通用电子、非 RISC-V 的 CPU 架构理论、非 RTL 编程。"
---

# RISC-V 芯片设计

面向 RISC-V 处理器的端到端数字设计技能，覆盖 RTL 编码 → 验证 → 逻辑综合 (Yosys) → 电路评估 → 物理设计 (RTL→GDSII)。

仅在请求涉及 RISC-V 处理器相关的 Verilog/SystemVerilog RTL、验证、综合、时序、CDC、DFT、物理设计产出物时才使用本技能。

## 核心能力

| # | 能力 | 工具/脚本 | 详细参考 |
|---|------|----------|---------|
| 1 | **RTL 代码生成** — 可综合 RISC-V 模块 | AI + 风格指南 | `references/verilog-style-guide.md` |
| 2 | **代码审查与 Lint** — 自动 + 人工检查 | `scripts/verilog_lint.py` | `references/lint-checklist.md` |
| 3 | **Testbench 生成** — directed / UVM 骨架 | `scripts/tb_generator.py` | `references/testbench-patterns.md` |
| 4 | **逻辑综合** — Yosys + ICSprout55-PDK | `scripts/yosys_synth.py` | `references/yosys-synthesis.md` |
| 5 | **电路评估** — QoR 评分 / 面积 / 时序 | `scripts/eval_circuit.py` | 见下方工作流 |
| 6 | **物理设计** — 网表 → GDSII 版图 | AI + OpenROAD 参考 | `references/physical-design.md` |
| 7 | **ASIC 流程指导** — SDC/CDC/DFT/STA | AI + ASIC 参考 | `references/asic-design-flow.md` |

## 工作流程

### 0. VM 远程连接

Yosys 综合和 OpenROAD 物理设计**推荐在 Linux VM 上远程执行**——统一的 Linux 环境兼容性最好。本地执行作为回退方案。

**交互方式**：当用户请求综合或物理设计时，Skill 会询问 VM 连接信息。用户通过 CLI 参数传入，不留存任何凭据：

```bash
# 每次执行时通过 --vm-host / --vm-user 传入 VM 地址
python scripts/yosys_synth.py rtl/*.v --top RV32Top --remote \
    --vm-host <VM_IP> --vm-user <username> -o synth_output
```

**可选持久化配置**（仅在用户明确要求时使用）：

- 环境变量: `RISCV_VM_HOST`, `RISCV_VM_USER`, `RISCV_VM_PORT`, `RISCV_VM_KEY`, `RISCV_VM_WORK_DIR`
- 配置文件: `~/.riscv_vm_config.json` — `{"host": "<VM_IP>", "user": "<username>"}`

**VM 环境准备**（一次性）：
```bash
ssh <username>@<VM_IP> "sudo apt install -y yosys"
ssh <username>@<VM_IP> "pip3 install gdstk"
# OpenROAD 需从 Precision-Innovations 下载预编译 .deb 包安装

# 配置 SSH 免密登录
ssh-keygen -t rsa -b 4096
ssh-copy-id <username>@<VM_IP>
```

**测试连接**：
```bash
python scripts/vm_runner.py check --vm-host <VM_IP> --vm-user <username>
```

### 1. 理解需求

识别需要哪种能力。常见信号：

| 用户说 | 对应能力 |
|--------|---------|
| "写一个 RISC-V ALU / 译码器 / CSR 模块……" | RTL 代码生成 |
| "审查/lint/检查这段 RTL 代码" | 代码审查与 Lint |
| "为这个模块生成 testbench" | Testbench 生成 |
| "综合这个设计 / 跑一下 Yosys" | **逻辑综合** |
| "评估一下电路 / QoR 怎么样 / 面积多大" | **电路评估** |
| "做物理设计 / 布局布线 / 出 GDSII" | **物理设计** |
| "怎么写 SDC / CDC 怎么检查 / DFT 怎么插" | ASIC 流程指导 |

### 2. 确定语言版本

- **Verilog-2001**：遗留设计、简单组合/时序逻辑、门级网表。Yosys 完全支持。
- **SystemVerilog**：新设计推荐。Yosys 对 interface/struct/enum 支持有限，RTL 核心逻辑建议用 Verilog-2001 风格子集。

新设计默认使用 SystemVerilog 可综合子集，但需注意 Yosys 兼容性约束。

### 3. 应用编码标准

生成或审查 RTL 时加载 `references/verilog-style-guide.md`。RISC-V 特定要点：
- 模块按流水线分级命名：`rv32_ifu`（取指）、`rv32_idu`（译码）、`rv32_exu`（执行）、`rv32_lsu`（访存）、`rv32_wbu`（写回）
- 使用标准 RISC-V 信号命名：`pc`, `instr`, `rs1_addr`, `rs2_addr`, `rd_addr`, `alu_op`, `mem_addr`, `wb_data`
- 其他同通用 ASIC 规则（异步复位同步释放、NB/BA 分离、case 有 default 等）

### 4. RTL 代码生成

根据规格说明：

1. 定义模块接口（端口、参数、时钟/复位极性）
2. 识别 RISC-V 标准接口信号（AXI / 自定义存储器接口）
3. 区分数据通路与控制逻辑
4. 编写 RTL，附带行内注释说明设计意图
5. 使用 `// synthesis translate_off/on` 包裹仅仿真代码
6. 标记模糊需求并提出假设
7. **修改 RTL 后，运行 `scripts/verilog_lint.py` 确认无错误**

### 5. 代码审查与 Lint

审查用户提供的代码时：

1. 对目标文件运行 `scripts/verilog_lint.py`
2. 对照 `references/lint-checklist.md` 进行人工审查
3. 将发现分类为：Error / Warning / Style
4. 检查 RISC-V 特定风险：
   - **旁路路径**：ALU 前递 mux 不能有组合环路
   - **寄存器堆**：多端口读写冲突检查
   - **CSR**：原子性读写、异常时正确更新
   - **流水线停顿**：stall/flush 信号对所有级正确传播
5. **任何修改后，重新运行 lint 确认零错误**
6. Testbench 文件使用 `--mode tb`

### 6. Testbench 生成

1. 确定 testbench 风格：简易定向、自检、UVM 骨架
2. 加载 `references/testbench-patterns.md`
3. 生成：时钟/复位发生器、DUT 实例化、激励接口、checker 占位、波形 dump
4. 包含 RISC-V 典型测试场景：
   - 寄存器读写、ALU 运算、分支/跳转
   - 流水线冲突（数据前递、控制冒险）
   - CSR 读写、异常/中断处理
   - 存储器访问（对齐/非对齐）

### 7. 逻辑综合（Yosys + ICSprout55-PDK）

当用户要求综合时：

#### 7.1 远程综合（推荐）

先确认 VM 连接已配置（见步骤 0），然后使用 `--remote`：

```bash
# 远程综合 (ICSprout55 PDK 工艺映射)
python scripts/yosys_synth.py rtl/*.v --top RV32Top \
    --pdk-path ~/icsprout55-pdk --clk-period 10.0 \
    --remote -o synth_output

# 远程综合 (无 PDK, generic 门级映射)
python scripts/yosys_synth.py rtl/*.v --top RV32Top \
    --remote -o synth_output

# 含大 Block RAM 的设计
python scripts/yosys_synth.py rtl/*.v --top RV32Top \
    --keep-hierarchy --remote -o synth_output
```

**自动完成**：上传 RTL → VM 运行 Yosys → 下载网表/报告 → 本地目录。

若 VM 不可达，脚本会提示并询问是否回退到本地执行。使用 `--no-fallback` 可禁止回退。

#### 7.2 本地综合（回退）

1. **确认顶层模块名和所有 RTL 文件**
2. **综合前 RTL 检查** — 脚本自动扫描综合常见陷阱：
   - 系统任务 (`$display`/`$readmemh`/`$monitor`) 未被 translate_off 包裹
   - `initial` 块导致 ROM/RAM 未初始化
   - 时钟信号未在模块端口中声明（`always @(posedge clk)` 的 clk 不是端口）
3. **检查 Yosys 是否安装**：
   - Linux: `sudo apt install yosys`
   - macOS: `brew install yosys`
   - Windows (MSYS2): `pacman -S mingw-w64-x86_64-yosys`
   - 安装后路径: `/mingw64/bin/yosys.exe` 或 `/usr/bin/yosys`
4. **运行综合脚本**：
   ```bash
   # 基本用法 (generic 门级映射)
   python scripts/yosys_synth.py rtl/*.v --top RV32Top -o synth_output
   
   # 含大 Block RAM 的设计，使用层次化综合
   python scripts/yosys_synth.py rtl/*.v --top RV32Top --keep-hierarchy -o synth_output
   
   # 使用 ICSprout55 PDK 进行工艺映射
   python scripts/yosys_synth.py rtl/*.v --top RV32Top \
       --pdk-path /path/to/icsprout55-pdk \
       --clk-period 10.0 -o synth_output
   ```
5. **若 PDK 未安装**，提示用户：
   ```
   git clone https://github.com/openecos-projects/icsprout55-pdk.git
   ```
   没有 PDK 时将使用 Yosys 内置 generic 门级映射（`$_NAND_`、`$_DFF_` 等）。

6. **验证综合结果**：
   - 检查输出中是否异常出现 `$_DLATCH_` (锁存器) — 说明组合逻辑不完整
   - 检查是否有模块单元数为 0 — 可能被优化掉（如 ROM 未初始化）
   - 统计门数、触发器数、存储器位数
7. **解析综合报告**，汇报面积、单元数、存储器、警告。**综合后建议运行电路评估**。
8. **若不需要 PDK 但需完整综合**，可手动编写 .ys 脚本：
   加载 `references/yosys-synthesis.md` 获取完整脚本模板和调试指南。

### 8. 电路评估

综合完成后或用户要求评估时：

1. 运行 `scripts/eval_circuit.py`：
   ```bash
   python scripts/eval_circuit.py synth_output \
       --target-freq-mhz 100 --area-budget 50000
   ```
2. 输出 QoR 评分 (0-100) 和评级 (A/B/C/D)
3. 汇总关键指标：面积、时序、单元分布、警告数
4. 给出具体优化建议（流水线深度 / 资源共享 / 存储器方案）

### 9. 物理设计（网表 → GDSII）

当用户要求物理设计时：

#### 9.1 自动远程流程（推荐）

```bash
# 综合后一键物理设计
python scripts/openroad_runner.py synth_output/riscv_pd_netlist.v \
    --top riscv_pd --pdk-path ~/icsprout55-pdk \
    --clk-period 5.0 --remote -o physical_design/
```

**自动完成**：上传网表+PDK → VM 运行 OpenROAD (Floorplan→PDN→Place→CTS→Route→Filler) → 下载 DEF/SPEF/报告。

**后续步骤**：
```bash
# 生成 GDSII 版图（gdstk）
python physical_design/def2gds.py

# 用 KLayout 查看
klayout physical_design/riscv.gds
```

#### 9.2 本地生成脚本（手动执行）

不指定 `--remote` 时，仅生成 OpenROAD TCL 脚本和 def2gds 转换脚本到输出目录，打印手动执行说明。

#### 9.3 物理设计详细流程

1. **加载** `references/physical-design.md` 和 `references/yosys-synthesis.md`
2. **确认工作环境**：
   - OpenROAD 只有 Linux 版本 — 若用户在 Windows，需 Ubuntu 22.04 VM
   - Windows 端用 Yosys 综合，SCP 传网表到 VM，VM 跑 OpenROAD
   - OpenROAD 必须用 Precision-Innovations 的 v2.0+ 预编译包（非主仓库旧版）
3. **阶段 A — 综合准备**：
   - 顶层模块必须有输出端口（debug 端口），否则 `opt_clean -purge` 清空全部逻辑
   - `synth -flatten` 后必须加 `techmap` 再 `abc`，否则 abc 提取 0 个 gate
   - 综合后检查 stat 报告：cells > 0, 无 `$_DLATCH_`
4. **阶段 B — 生成物理设计脚本**：
   - 基于 `physical-design.md` 中的完整 TCL 模板（已验证可用）
   - 关键 v2.0 语法差异：
     - PDN: `add_global_connection` + `global_connect`（无参数），非旧版 `global_connect -net VDD -type tiehi`
     - 报告: `command > file`，非 `-out_file file`
     - 无 `write_gds` 命令
   - 脚本必须用文件传（SCP），不能用 SSH heredoc（`$` 变量被 bash 吞掉）
5. **阶段 C — 在 VM 执行**：
   ```
   openroad -no_init -exit run_pd_vm.tcl
   ```
   按阶段推进：Floorplan → PDN → tie cells → Placement → CTS → tracks → pin placement → Routing → Filler → Export
6. **阶段 D — DEF → GDSII**：
   - OpenROAD v2.0 无 `write_gds`，用 gdstk Python 库转换
   - 读取 PDK 标准单元 GDS + 解析 DEF COMPONENTS 段 → 生成顶层 GDS
7. **阶段 E — 验收**：
   - 用 KLayout GUI（Windows 版）打开 `riscv.gds` 查看版图
   - 确认顶层 cell 中有标准单元布局
8. **必须按顺序执行的命令**（遗漏会导致错误）：
   - `make_tracks` 在布线前（否则 GRT-0701）
   - `place_pins` 在布线前（否则 GRT-0042 pin 无几何信息）
   - `insert_tiecells` 在 PDN 后（否则 DRT-0305 POWER net 错误）
   - `techmap` 在 `abc` 前（否则映射 0 个 gate）

## 资源目录

| 文件 | 用途 | 何时加载 |
|------|------|---------|
| `references/verilog-style-guide.md` | 编码规范 | RTL 生成、代码审查 |
| `references/lint-checklist.md` | Lint 清单 | 代码审查、综合调试 |
| `references/testbench-patterns.md` | Testbench 模板 | Testbench 创建 |
| `references/yosys-synthesis.md` | Yosys 综合指南 | **逻辑综合** |
| `references/physical-design.md` | 物理设计指南 (RTL→GDSII) | **物理设计** |
| `references/asic-design-flow.md` | ASIC 流程参考 (SDC/CDC/DFT/STA) | 物理设计、时序问题 |
| `scripts/verilog_lint.py` | RTL 静态分析 | 代码审查 |
| `scripts/tb_generator.py` | Testbench 脚手架 | Testbench 创建 |
| `scripts/yosys_synth.py` | Yosys 综合脚本 (支持 --remote) | **逻辑综合** |
| `scripts/eval_circuit.py` | 电路评估 / QoR | **电路评估** |
| `scripts/vm_runner.py` | VM SSH/SCP 远程执行基础模块 | 远程综合 / 远程物理设计 |
| `scripts/openroad_runner.py` | OpenROAD 物理设计自动化 (支持 --remote) | **物理设计** |
| `scripts/verilog_lint.py` 的 A11/A12/B9/B10 规则 | 综合专项 Lint | 综合前 RTL 检查 |

## RISC-V ISA 快速参考

### RV32I 基础指令集

| 类型 | 指令 | 数据通路 |
|------|------|---------|
| R-type | ADD, SUB, SLL, SLT, SLTU, XOR, SRL, SRA, OR, AND | rs1 op rs2 → rd |
| I-type | ADDI, SLTI, SLTIU, XORI, ORI, ANDI, SLLI, SRLI, SRAI | rs1 op imm → rd |
| I-type Load | LB, LH, LW, LBU, LHU | mem[rs1+imm] → rd |
| S-type | SB, SH, SW | rs2 → mem[rs1+imm] |
| B-type | BEQ, BNE, BLT, BGE, BLTU, BGEU | if rs1 op rs2: PC += imm |
| U-type | LUI, AUIPC | imm → rd |
| J-type | JAL | PC+4 → rd, PC += imm |
| I-type JALR | JALR | PC+4 → rd, PC = rs1+imm |

### CSR 关键地址 (RV32)

| 地址 | 名称 | 描述 |
|------|------|------|
| 0x300 | mstatus | 机器状态 |
| 0x304 | mie | 中断使能 |
| 0x305 | mtvec | 陷阱向量基址 |
| 0x341 | mepc | 异常程序计数器 |
| 0x342 | mcause | 陷阱原因 |
| 0x343 | mtval | 陷阱值 |
| 0x344 | mip | 中断 pending |

## 重要说明

- **综合目标**：默认面向 ASIC 可综合子集，目标工艺 ICSprout55-PDK (55nm)。仅当用户明确要求 FPGA 时才使用 FPGA 结构。
- **Yosys 兼容性**：避免 SystemVerilog interface/struct/enum 等 Yosys 不完全支持的特性。RISC-V 核心 RTL 优先使用 Verilog-2001 可综合子集。
- **时钟域**：多时钟设计标记 CDC 问题；建议使用同步器或握手协议。
- **复位策略**：ASIC 推荐异步复位 / 同步释放。每个时钟域配备复位同步器。
- **不要猜测工具版本**：商业工具请确认版本。Yosys 使用 `yosys --version` 检查。
- **综合前必检项**：
  - 所有 `always @(posedge clk)` 的 clk 必须是模块端口
  - `$display`/`$readmemh` 等系统任务用 `// synthesis translate_off/on` 包裹
  - 组合逻辑 `always @(*)` 块中所有输出信号必须有默认值（防 latch）
  - ROM 需有综合兼容的初始化方式（小型用 case 语句，大型用 IP 宏）
- **Yosys TCL 语法警告**：`>` 是 selection 操作符不能做文件重定向，`echo` 仅支持 on/off，文件输出用 `tee -q -o <file> <cmd>`
- **大存储器设计**：含 8Kb+ Block RAM 的设计优先用 `--keep-hierarchy` + `memory -nomap`，避免 `flatten` 导致存储器内部逻辑不可见
- **OpenROAD v2.0 关键差异**（Precision-Innovations 发行版）：
  - PDN: `add_global_connection` + `global_connect`，不是旧版 `global_connect -net -type`
  - 无 `write_gds` — 用 gdstk Python 库转 DEF→GDSII
  - 报告输出用 Tcl `> file` 重定向，不支持 `-out_file` 参数
  - CTS 的 `-buf_list` 填标准单元名（如 `BUFX2H7L`），不是 LEF 路径
  - `make_tracks` 布线前必须执行；`place_pins` 给顶层端口分配金属层
  - `insert_tiecells "TIEHIH7L/Z"` 处理 Yosys 产生的 `1'b1`/`1'b0` 常量
  - DRT 可设 `-droute_end_iter 5` 限制迭代轮数，接受少量违规
  - filler 和布线后 detailed_placement 用 `catch {}` 包裹
  - 脚本文件用 SCP 传 VM，不能用 SSH heredoc（`$` 变量被 bash 吞掉）
