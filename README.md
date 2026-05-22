# RISC-V 芯片设计 — Claude Code Skill

面向 RISC-V 处理器的端到端数字设计技能，覆盖 **RTL 编码 → 验证 → 逻辑综合 (Yosys) → 电路评估 → 物理设计 (RTL→GDSII)**，基于 **ICSprout55 55nm** 开源工艺。

## 目录

- [前置条件](#前置条件)
- [安装](#安装)
- [快速开始](#快速开始)
- [核心能力](#核心能力)
- [目录结构](#目录结构)
- [脚本详解](#脚本详解)
  - [verilog_lint.py — RTL 静态检查](#verilog_lintpy--rtl-静态检查)
  - [tb_generator.py — Testbench 生成](#tb_generatorpy--testbench-生成)
  - [yosys_synth.py — 一键综合](#yosys_synthpy--一键综合)
  - [eval_circuit.py — 电路评估](#eval_circuitpy--电路评估)
- [物理设计流程 (RTL→GDSII)](#物理设计流程-rtlgdsii)
- [参考文档](#参考文档)
- [常见问题](#常见问题)
- [贡献](#贡献)
- [许可证](#许可证)

## 前置条件

| 工具 | 用途 | 安装 |
|------|------|------|
| [Claude Code](https://claude.ai/code) | Skill 运行平台 | 官方安装指南 |
| Python 3.10+ | 脚本运行 | `python.org` 或包管理器 |
| [Yosys](https://github.com/YosysHQ/yosys) | RTL 逻辑综合 | `apt install yosys` / `pacman -S mingw-w64-x86_64-yosys` / `brew install yosys` |
| [OpenROAD v2.0+](https://github.com/Precision-Innovations/OpenROAD/releases) | 物理设计 (仅 Linux) | Precision-Innovations 预编译 `.deb` 包 |
| [gdstk](https://github.com/heitzmann/gdstk) | DEF → GDSII 转换 | `pip install gdstk` |
| [KLayout](https://www.klayout.de) | GDSII 版图查看 | Windows/macOS/Linux GUI |

> **注意**：OpenROAD 只有 Linux 版本。Windows 用户需搭配 Ubuntu 22.04 虚拟机完成物理设计，详见 [物理设计流程](#物理设计流程-rtlgdsii)。

## 安装

将此仓库克隆到 Claude Code 的 skills 目录：

```bash
# Linux / macOS
mkdir -p ~/.claude/skills
git clone https://github.com/<your-username>/riscv-design-skill.git ~/.claude/skills/riscv-design

# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills"
git clone https://github.com/<your-username>/riscv-design-skill.git "$env:USERPROFILE\.claude\skills\riscv-design"
```

安装完成后，重启 Claude Code 或输入 `/skills` 确认 `riscv-design` 出现在可用技能列表中。

## 快速开始

在 Claude Code 对话中直接描述你的需求，Skill 将自动激活：

```
# RTL 生成
"写一个 RISC-V RV32I 的 ALU 模块，支持 ADD/SUB/AND/OR/XOR/SLT"

# 代码审查
"审查我的 ControlUnit.v，检查综合和时序问题"

# 逻辑综合
"用 Yosys 综合我的 RISC-V 处理器，目标工艺 ICSprout55"

# 物理设计
"对综合后的网表做物理设计，生成 GDSII 版图"
```

也可以手动调用 Skill：

```
/riscv-design
```

## 核心能力

| # | 能力 | 工具链 | 产出物 |
|---|------|--------|--------|
| 1 | **RTL 代码生成** | AI + 风格指南 | 可综合 Verilog/SystemVerilog 模块 |
| 2 | **代码审查与 Lint** | `verilog_lint.py` + 人工审查 | 分类问题清单 (Error/Warning/Style) |
| 3 | **Testbench 生成** | `tb_generator.py` | 定向 / 自检 / UVM 骨架 testbench |
| 4 | **逻辑综合** | Yosys + ICSprout55-PDK | 门级网表、面积/时序报告 |
| 5 | **电路评估** | `eval_circuit.py` | QoR 评分 (0-100)、面积、时序分析 |
| 6 | **物理设计** | OpenROAD v2.0 + gdstk | DEF 网表、GDSII 版图 |
| 7 | **ASIC 流程指导** | AI + 参考文档 | SDC/CDC/DFT/STA 约束与检查 |

## 目录结构

```
riscv-design/
├── SKILL.md                          # 主技能文件 (Claude Code 入口)
├── README.md                         # 本文件
├── agents/
│   └── openai.yaml                   # Agent 配置
├── references/                       # 参考文档
│   ├── verilog-style-guide.md        # RTL 编码规范 (命名、复位、FSM)
│   ├── lint-checklist.md             # RTL Lint 清单 (A/B/C/D 四类)
│   ├── testbench-patterns.md         # Testbench 模板与模式
│   ├── yosys-synthesis.md            # Yosys 综合实战指南 (ICSprout55)
│   ├── physical-design.md            # 物理设计完整流程 (RTL→GDSII)
│   └── asic-design-flow.md           # ASIC 流程参考 (SDC/CDC/DFT/STA)
├── scripts/                          # Python 工具脚本
│   ├── verilog_lint.py               # RTL 静态 Lint 检查器
│   ├── tb_generator.py               # Testbench 脚手架生成器
│   ├── yosys_synth.py                # Yosys 综合自动化脚本
│   └── eval_circuit.py               # 电路评估与 QoR 评分
├── .claude/
│   └── settings.local.json           # 权限配置
└── .vscode/
    └── settings.json                 # VS Code 设置
```

## 脚本详解

### verilog_lint.py — RTL 静态检查

基于正则表达式的启发式 Verilog/SystemVerilog linter，零外部依赖，可配合 Verilator/Verible/Slang 使用。

```bash
# 基本用法
python scripts/verilog_lint.py src/ALU.v

# 指定模式 (rtl / tb)
python scripts/verilog_lint.py src/ALU.v --mode rtl

# 启用外部 linter
python scripts/verilog_lint.py src/ALU.v --external auto
python scripts/verilog_lint.py src/ALU.v --external verilator

# 详细输出
python scripts/verilog_lint.py src/ALU.v --verbose
```

**检查项**：

| 代码 | 级别 | 说明 |
|------|------|------|
| A1 | WARNING | 敏感信号列表不完整 |
| A3 | WARNING | case 语句缺少 default |
| A7 | ERROR | RTL 中出现 initial 块 |
| A8 | ERROR | RTL 中出现 #delay |
| B1 | WARNING | 锁存器推断风险 |
| B3 | WARNING | 隐式 wire (未声明标识符) |
| C3 | WARNING | 时序块缺少复位 |
| C7 | INFO | 多时钟边沿 (CDC 提醒) |
| D1 | STYLE | Tab 字符检测 |

### tb_generator.py — Testbench 生成

自动解析模块端口，生成包含时钟/复位发生器、DUT 实例化、激励框架的 testbench。

```bash
# 生成 Verilog-2001 testbench (Icarus Verilog 兼容)
python scripts/tb_generator.py src/ALU.v

# 生成 SystemVerilog testbench
python scripts/tb_generator.py src/ALU.v --sv

# 指定时钟周期和输出路径
python scripts/tb_generator.py src/ALU.v --clk_period_ns 20 --output tb_alu.sv

# 生成 UVM 骨架
python scripts/tb_generator.py src/ALU.v --uvm-skeleton
```

### yosys_synth.py — 一键综合

自动检测 PDK、生成 Yosys 脚本、运行综合、收集报告。支持 generic 和 PDK 映射两种模式。

```bash
# Generic 综合 (Yosys 内置单元)
python scripts/yosys_synth.py src/*.v --top riscv_pd -o synth_output

# ICSprout55 PDK 工艺映射
python scripts/yosys_synth.py src/*.v --top riscv_pd \
    --pdk-path ~/icsprout55-pdk \
    --clk-period 10.0 -o synth_output

# 含大 Block RAM 的层次化综合
python scripts/yosys_synth.py src/*.v --top RV32Top \
    --keep-hierarchy -o synth_output

# 输出 JSON 网表
python scripts/yosys_synth.py src/*.v --top riscv_pd --json -o synth_output
```

**输出物** (`synth_output/`)：
- `<top>_netlist.v` — 映射后门级网表
- `<top>_stat.txt` — 面积/单元统计
- `<top>_timing.txt` — 时序报告
- `<top>.json` — Yosys JSON 网表 (需 `--json`)
- `yosys_log.txt` — 完整综合日志
- `pre_synth_check.txt` — 综合前 RTL 检查

### eval_circuit.py — 电路评估

解析综合输出，生成 QoR (Quality of Results) 评分报告。评估面积、时序、单元分布、违规项等多维度指标。

```bash
# 评估综合结果
python scripts/eval_circuit.py synth_output

# 设定约束目标
python scripts/eval_circuit.py synth_output \
    --constraints 100 50000    # 100MHz 目标频率、50000um² 面积预算

# 对比两次综合
python scripts/eval_circuit.py synth_output_v1 --compare synth_output_v2
```

**QoR 评分维度**（满分 100）：
- 面积利用率
- 时序收敛度 (关键路径 vs 目标频率)
- 单元分布合理性 (组合/时序比)
- 设计规则违规数
- 存储器/宏单元使用效率

## 物理设计流程 (RTL→GDSII)

完整流程已通过 **ICSprout55 55nm (1P6M)** 工艺端到端验证。详细指南见 `references/physical-design.md`。

### 架构概览

```
Windows (MSYS2)                    Ubuntu 22.04 VM
─────────────────                   ──────────────────
RTL .v ──(Yosys)──▶ flat_netlist.v
                        │
                        ├──SCP──▶  ~/design/
                        │
                        │           read_lef / read_liberty
PDK LEF/liberty/GDS ──SCP──▶       floorplan → PDN → place
                        │           CTS → route → filler
                        │           write_def → riscv.def
                        │
                        │           gdstk: DEF + GDS → riscv.gds
                        │
riscv.gds ◀──SCP──       │
```

### 关键步骤

1. **Yosys 综合** (Windows/Linux) — `synth -flatten` → `techmap` → `abc -liberty` → `dfflibmap`
2. **SCP 传输** — 网表 + PDK 文件传到 Ubuntu VM
3. **OpenROAD 物理设计** (Ubuntu VM) — Floorplan → PDN → Place → CTS → Route → Filler
4. **DEF → GDSII 转换** — gdstk Python 脚本
5. **版图查看** — KLayout GUI

### OpenROAD v2.0 关键注意事项

> 必须使用 [Precision-Innovations/OpenROAD](https://github.com/Precision-Innovations/OpenROAD/releases) 的 v2.0+ 预编译包，API 与旧版本显著不同。

| 要点 | 说明 |
|------|------|
| PDN 语法 | `add_global_connection` + `global_connect`（无参数），非旧版语法 |
| 报告输出 | `command > file` Tcl 重定向，不支持 `-out_file` |
| 无 `write_gds` | 用 gdstk Python 库转换 DEF → GDSII |
| `make_tracks` | 布线前必须执行 |
| `place_pins` | 给顶层端口分配金属层 |
| `insert_tiecells` | PDN 后插入 TIE 单元处理常量 `1'b1`/`1'b0` |
| 脚本传输 | SCP 传 `.tcl` 文件，不能用 SSH heredoc |

## 参考文档

| 文件 | 内容 | 适用场景 |
|------|------|---------|
| `references/verilog-style-guide.md` | 命名规范、可综合子集规则、FSM 模式 | RTL 编码、代码审查 |
| `references/lint-checklist.md` | 四类 Lint 清单 (A/B/C/D) | 代码审查、综合调试 |
| `references/testbench-patterns.md` | 定向/自检/UVM testbench 模板 | Testbench 编写 |
| `references/yosys-synthesis.md` | Yosys 综合实战指南、常见错误 | 逻辑综合、调试 |
| `references/physical-design.md` | OpenROAD 物理设计、DEF→GDSII | 物理设计 |
| `references/asic-design-flow.md` | SDC/CDC/DFT/STA 约束参考 | 时序约束、DFT 规划 |

## 常见问题

### Skill 未被识别？

```bash
# 确认目录结构正确
ls ~/.claude/skills/riscv-design/SKILL.md

# 重启 Claude Code 或运行
/skills
```

### Yosys 未找到？

```bash
# 确认安装
which yosys
yosys --version

# Windows (MSYS2 MINGW64)
pacman -S mingw-w64-x86_64-yosys
# 安装后路径: /mingw64/bin/yosys.exe
```

### PDK 未安装？

```bash
git clone https://github.com/openecos-projects/icsprout55-pdk.git ~/icsprout55-pdk
```

没有 PDK 时，`yosys_synth.py` 会使用 Yosys 内置 generic 门级映射。

### 综合后 cell 数为 0？

顶层模块缺少输出端口。Yosys 的 `opt_clean -purge` 会删除无外部可见效果的内部逻辑。为顶层模块添加 debug 输出端口即可。

### Windows 如何做物理设计？

OpenROAD 无 Windows 版本。推荐方案：
1. Windows 端用 Yosys 完成综合
2. 安装 VMware/Ubuntu 22.04 虚拟机
3. SCP 传输文件到 VM
4. VM 端运行 OpenROAD
5. 结果 SCP 传回 Windows 用 KLayout 查看

详见 `references/physical-design.md` 中的完整步骤。

## 贡献

欢迎提交 Issue 和 Pull Request。

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/your-feature`)
3. 提交更改 (`git commit -m 'Add some feature'`)
4. 推送到分支 (`git push origin feature/your-feature`)
5. 创建 Pull Request

## 许可证

MIT License — 详见 [LICENSE](LICENSE) 文件。

---

**适用工艺**：ICSprout55-PDK (55nm 1P6M)  
**标准单元库**：ics55_LLSC_H7CL (LVT, 7-track, CoreSite 0.2×1.4µm)  
**验证状态**：端到端 RTL→GDSII 流程已验证通过 (RISC-V RV32I 处理器, 391 cells)
