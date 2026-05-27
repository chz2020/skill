# Yosys 逻辑综合指南 (ICSprout55-PDK 实战版)

## 概述

Yosys 是开源 Verilog/SystemVerilog 综合框架。本文档基于实战验证的 RISC-V 处理器综合流程编写。

## 安装

```bash
# Windows (MSYS2 MINGW64)
pacman -S mingw-w64-x86_64-yosys
# 安装路径: /mingw64/bin/yosys.exe

# Linux
sudo apt install yosys

# macOS
brew install yosys
```

## ICSprout55 PDK 目录结构

```
~/icsprout55-pdk/
└── IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL/
    ├── lef/
    │   ├── ics55_LLSC_H7CL.lef           # 标准单元 LEF
    │   └── ics55_LLSC_H7CL_ant.lef       # 天线 LEF
    ├── liberty/
    │   ├── ics55_LLSC_H7CL_ss_rcworst_1p08_125_nldm.lib  # setup corner
    │   ├── ics55_LLSC_H7CL_ss_cworst_1p08_m40_nldm.lib
    │   ├── ics55_LLSC_H7CL_typ_tt_1p2_25_nldm.lib
    │   └── ...
    └── gds/
        └── ics55_LLSC_H7CL.gds
```

### 标准单元类型 (ics55_LLSC_H7CL)

| 类型 | 前缀示例 | 说明 |
|------|---------|------|
| 反相器 | INVX0P5H7L ~ INVX10H7L | 多种驱动强度 |
| 缓冲器 | BUFX0P5H7L ~ BUFX20H7L | CTS 关键单元 |
| NAND | NAND2X0P5H7L ~ NAND4X0P5H7L | 基本门 |
| NOR | NOR2X0P5H7L ~ NOR4X0P5H7L | 基本门 |
| AOI | AOI21X0P5H7L ~ AOI31X0P5H7L | 与或非门 |
| OAI | OAI21X0P5H7L | 或与非门 |
| MUX | MUX2X0P5H7L | 选择器 |
| XOR/XNOR | XOR2X0P5H7L, XNOR2X0P5H7L | 异或/同或 |
| DFF | DFFRQX2H7L | D 触发器 |
| TIE | TIEHIH7L, TIELOH7L | 恒高/恒低 |
| FILL | FILLER1H7L ~ FILLER64H7L, FILLCAP* | 填充/去耦 |

## 实战综合流程 (已验证)

### 完整 .ys 脚本

```tcl
# === RISC-V Processor Synthesis for ICSprout55 ===
# 文件: synth_flat.ys

# 1. 读入所有 RTL 文件 (使用绝对路径或相对路径)
read_verilog -nomeminit -I <local_project_path>/src \
  <local_project_path>/src/ALU.v \
  <local_project_path>/src/ControlUnit.v \
  <local_project_path>/src/DM.v \
  <local_project_path>/src/EXT.v \
  <local_project_path>/src/Flopr.v \
  <local_project_path>/src/IM.v \
  <local_project_path>/src/IR.v \
  <local_project_path>/src/MUX_2to1_A.v \
  <local_project_path>/src/MUX_3to1.v \
  <local_project_path>/src/MUX_3to1_B.v \
  <local_project_path>/src/MUX_3to1_LMD.v \
  <local_project_path>/src/NPC.v \
  <local_project_path>/src/PC.v \
  <local_project_path>/src/RF.v \
  <local_project_path>/src/riscv_pd.v

# 2. 建立层次
hierarchy -check -top riscv_pd

# 3. synth -flatten: 自动处理 proc/flatten/opt/fsm
synth -flatten -top riscv_pd

# 4. 工艺映射 - 三部曲
read_liberty -lib C:/Users/24183/icsprout55-pdk/IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL/liberty/ics55_LLSC_H7CL_ss_rcworst_1p08_125_nldm.lib
dfflibmap -liberty C:/Users/24183/icsprout55-pdk/IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL/liberty/ics55_LLSC_H7CL_ss_rcworst_1p08_125_nldm.lib

# 关键: techmap 必须在 abc 之前!
# Yosys synth 后内部是 $_NAND_/$_DFF_ 等通用单元
# 需要 techmap 转换为基本门后 abc 才能识别
techmap

# 5. abc 映射到 PDK 标准单元
abc -liberty C:/Users/24183/icsprout55-pdk/IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL/liberty/ics55_LLSC_H7CL_ss_rcworst_1p08_125_nldm.lib

# 6. 清理与检查
opt_clean
check

# 7. 统计报告
tee -q -o <local_project_path>/physical_design/post_map_stat.txt \
    stat -liberty C:/Users/24183/icsprout55-pdk/IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL/liberty/ics55_LLSC_H7CL_ss_rcworst_1p08_125_nldm.lib

# 8. 输出扁平网表
write_verilog -noexpr -noattr <local_project_path>/physical_design/riscv_flat_netlist.v
```

### 运行

```bash
# Windows MSYS2
cd "<local_project_path>/physical_design"
yosys synth_flat.ys
```

## 常见问题与解决方案

### 1. 网表为空/零单元 (最重要!)

**症状**: 综合后 `stat` 显示 0 cells, 网表只有端口声明无逻辑。

**原因**: 顶层模块没有输出端口, `opt_clean -purge` 把内部逻辑全删了。

**修复**: 在顶层模块添加 debug 输出端口:
```verilog
module riscv_pd (
    input  clk, rst,
    output [31:0] debug_PC,        // 程序计数器
    output [31:0] debug_NPC,       // 下一 PC
    output [31:0] debug_PCA4,      // PC+4
    output [31:0] debug_ins,       // 指令
    output [31:0] debug_ALU_result,// ALU 结果
    output [31:0] debug_RD1,       // 寄存器堆读口1
    output [31:0] debug_RD2,       // 寄存器堆读口2
    output [31:0] debug_WD,        // 写回数据
    output [4:0]  debug_RD         // 目的寄存器
);
    // ... 实例化所有子模块, 连接 debug 端口 ...
endmodule
```

### 2. ABC 提取 0 个 gate

**症状**: `abc` 运行成功但 "Extracted 0 gates"。

**原因**: `synth -flatten` 后内部单元是 `$_NAND_`/`$_DFF_` 等 Yosys 通用门, `abc` 无法识别。

**修复**: 在 `abc` 之前加 `techmap`:
```tcl
synth -flatten -top riscv_pd
techmap      # 将 $_* 转换为基本门
abc -liberty ...  # 现在 abc 能看到门了
```

### 3. ROM/IM 被优化消失

**症状**: 综合后 IM 单元数为 0。

**原因**: `$readmemh` 在 `initial` 块中, Yosys 默认忽略 `initial`。ROM 内容全 X → 输出恒 X → 下游逻辑被判定为 dead code。

**修复**:
```bash
# 方法1: read_verilog 加 -nomeminit 跳过 $readmemh 错误
read_verilog -nomeminit ...

# 方法2: 小型 ROM 用 generate + case 实现 (综合友好)
always @(*) begin
    case(addr)
        10'd0: Ins = 32'h00000093;
        default: Ins = 32'h00000013;
    endcase
end
```

### 4. 顶层常量产生 POWER 信号网

**症状**: OpenROAD 详细布线报 "Net one_ of signal type POWER is not routable"。

**原因**: Yosys 对 `1'h1`/`1'h0` 使用直接赋值, 不用 TIE 单元。

**修复**: 在 OpenROAD PDN 阶段后执行:
```tcl
insert_tiecells "TIEHIH7L/Z" -prefix "TIE_ONE_"
insert_tiecells "TIELOH7L/Z" -prefix "TIE_ZERO_"
```

### 5. Latch 推断

**症状**: 综合报告出现 `$_DLATCH_` 单元。

**原因**: 组合逻辑 `always @(*)` 块中信号未在所有条件下赋值 (case 缺 default, if 缺 else)。

**修复**:
```verilog
always @(*) begin
    result = default_val;  // 默认值防 latch
    case (state)
        S_ALU: result = a + b;
        S_MEM: result = mem_data;
    endcase
end
```

### 6. Yosys TCL 语法陷阱

```tcl
# ❌ 错误: > 在 Yosys 中是 selection 操作符
stat > output.txt

# ✅ 正确: 使用 tee
tee -q -o output.txt stat

# ❌ 错误: echo 不支持字符串
echo "Synthesis Complete"

# ✅ 正确: 使用 puts 或注释
# === Synthesis Complete ===
```

### 7. 中文路径/文件名

Yosys 可能无法正确处理中文路径。建议将工作目录改名为纯 ASCII:
```bash
# ❌ E:\中文路径\riscv_design\  — 可能编码问题
# ✅ E:\project\riscv_design\ — 正常工作
```

## 综合优化策略

### 面积优化

```tcl
# 减少面积 (使用小驱动强度单元)
abc -liberty <lib> -script +/abc/script/compress2rs
# 或者
abc -liberty <lib> -constr <sdc_file>
```

### 时序优化

```tcl
# target 更快的单元
abc -liberty <lib> -D <target_delay_ps>
```

### 调试用 Generic 综合

如果 PDK 映射有问题, 先用 generic 综合验证:
```tcl
synth -flatten -top riscv_pd
opt_clean
stat
write_verilog -noexpr output.v  # 查看 $_NAND_/$_DFF_ 等通用单元
```

## 与商业工具对比

| 能力 | Yosys | Design Compiler |
|------|-------|-----------------|
| Verilog-2001 | ✅ 完整 | ✅ |
| SystemVerilog 综合子集 | ✅ 大部分 | ✅ |
| Liberty 支持 | ✅ NLDM | ✅ NLDM/CCS |
| SDC 约束 | 基本 | 完整 |
| DFT 插入 | 手动 | 自动 |
| 多 corner 优化 | 有限 | 完整 |
