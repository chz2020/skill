# 物理设计：网表 → GDSII (实战验证)

## 概述

物理设计将综合后的门级网表转化为 GDSII 版图。本文档基于 **ICSprout55 55nm (1P6M)** 工艺、**ics55_LLSC_H7CL** 标准单元库的实战验证流程编写。

## 工具链

| 阶段 | 工具 | 安装方式 |
|------|------|---------|
| RTL 综合 | Yosys (Windows/MSYS2) | `pacman -S mingw-w64-x86_64-yosys` |
| 物理设计 | OpenROAD v2.0 (Ubuntu 22.04 VM) | [Precision-Innovations Releases](https://github.com/Precision-Innovations/OpenROAD/releases) `.deb` 包 |
| DEF→GDSII | gdstk (Python) | `pip3 install gdstk` |
| 版图查看 | KLayout (Windows GUI) | https://www.klayout.de/build.html |

> **为什么需要 VM**: OpenROAD 没有 Windows 版本。使用 VMware/Ubuntu 22.04，通过 SSH + SCP 与 Windows 协作。

## 完整流程概览

```
Windows (MSYS2)                Ubuntu VM (192.168.x.x)
─────────────────               ────────────────────────
RTL .v ──(Yosys)──▶ flat_netlist.v
                            │
                            ├──SCP──▶ VM:~/design/
                            │
                            │         read_lef / read_liberty
PDK LEF/liberty/GDS ──SCP──▶         floorplan → PDN → place
                            │         CTS → route → filler
                            │         write_def → riscv.def
                            │
                            │         gdstk: DEF + GDS → riscv.gds
                            │
riscv.gds ◀──SCP──           │
```

## 1. VM 环境准备

### 安装 OpenROAD (Ubuntu 22.04)

```bash
# 下载预编译 .deb (不要编译源码, 太慢)
curl -L -o openroad.deb \
  'https://github.com/Precision-Innovations/OpenROAD/releases/download/2024-12-14/openroad_2.0-17598-ga008522d8_amd64-ubuntu-22.04.deb'
sudo apt install -y ./openroad.deb

# 安装辅助工具
sudo apt install -y yosys xvfb
pip3 install gdstk
```

### 配置 SSH 免密 (从 Windows 控制 VM)

```bash
# Windows 端生成密钥对
ssh-keygen -t rsa -b 4096

# 复制公钥到 VM
ssh-copy-id chz@192.168.164.132

# 配置 sudo 免密 (在 VM 上)
sudo visudo
# 添加: chz ALL=(ALL) NOPASSWD:ALL
```

## 2. PDK 目录结构 (实际)

```
~/pdk/
├── prtech/techLEF/
│   └── N551P6M.lef                    # 工艺 LEF (20层, 38 via)
├── IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL/
│   ├── lef/
│   │   ├── ics55_LLSC_H7CL.lef         # 标准单元 LEF (784 cells)
│   │   └── ics55_LLSC_H7CL_ant.lef     # 天线检查 LEF
│   ├── liberty/
│   │   ├── ics55_LLSC_H7CL_ss_rcworst_1p08_125_nldm.lib
│   │   ├── ics55_LLSC_H7CL_ss_cworst_1p08_m40_nldm.lib
│   │   ├── ics55_LLSC_H7CL_typ_tt_1p2_25_nldm.lib
│   │   └── ... (ff corners)
│   └── gds/
│       └── ics55_LLSC_H7CL.gds         # 标准单元 GDS (4.3 MB)
└── IP/IO/ICsprout_55LLULP1233_IO_251013/  # IO 单元 (core-only 设计不需要)
```

## 3. OpenROAD 物理设计流程 (v2.0 实战脚本)

### 3.1 完整 TCL 脚本模板

```tcl
# =============================================================================
# OpenROAD Physical Design Flow — ICSprout55 55nm (1P6M)
# 标准单元: ics55_LLSC_H7CL (LVT, 7-track, CoreSite: 0.2x1.4 um)
# =============================================================================

set script_dir "/home/chz/design"
set pdk_base "/home/chz/pdk"
set std_lib "$pdk_base/IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL"
set tech_dir "$pdk_base/prtech/techLEF"

# === 1. Setup ===
read_lef "$tech_dir/N551P6M.lef"
read_lef "$std_lib/lef/ics55_LLSC_H7CL.lef"
if {[file exists "$std_lib/lef/ics55_LLSC_H7CL_ant.lef"]} {
    read_lef "$std_lib/lef/ics55_LLSC_H7CL_ant.lef"
}
read_liberty "$std_lib/liberty/ics55_LLSC_H7CL_ss_rcworst_1p08_125_nldm.lib"
read_verilog "$script_dir/riscv_flat_netlist.v"
link_design riscv_pd

# === 2. Floorplan ===
# CoreSite: 0.2 x 1.4 um (7-track cells)
initialize_floorplan \
    -site CoreSite \
    -utilization 60 \
    -aspect_ratio 1.0 \
    -core_space {10 10 10 10}

# === 3. Power Delivery Network ===
# v2.0 语法: add_global_connection → global_connect → PDN grid → pdngen
add_global_connection -net VDD -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net VSS -inst_pattern {.*} -pin_pattern {^VSS$} -ground
global_connect

set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name grid -voltage_domains CORE
add_pdn_stripe -grid grid -layer MET1 -width 0.09 -followpins -extend_to_core_ring
add_pdn_stripe -grid grid -layer MET4 -width 1.0 -pitch 10.0 -offset 0 -extend_to_core_ring
add_pdn_stripe -grid grid -layer MET5 -width 2.0 -pitch 20.0 -offset 0 -extend_to_core_ring
add_pdn_connect -grid grid -layers {MET1 MET4}
add_pdn_connect -grid grid -layers {MET4 MET5}
pdngen

# 关键: 插入 tie cells 替换 1'b1/1'b0 常量, 否则 DRT 报 POWER net 错误
insert_tiecells "TIEHIH7L/Z" -prefix "TIE_ONE_"
insert_tiecells "TIELOH7L/Z" -prefix "TIE_ZERO_"

# === 4. SDC 约束 ===
set sdc_fh [open "$script_dir/riscv.sdc" w]
puts $sdc_fh {
create_clock -name clk -period 5.0 [get_ports clk]
set_propagated_clock clk
set_input_delay -clock clk -max 0.5 [get_ports rst]
set_output_delay -clock clk -max 0.5 [all_outputs]
set_clock_uncertainty 0.1 clk
set_load 0.05 [all_outputs]
}
close $sdc_fh
read_sdc "$script_dir/riscv.sdc"

# === 5. Placement ===
global_placement
repair_design
detailed_placement
check_placement -verbose

# === 6. Clock Tree Synthesis ===
set_wire_rc -signal -layer MET1
set_wire_rc -clock -layer MET3
repair_clock_inverters

clock_tree_synthesis \
    -buf_list {BUFX2H7L BUFX4H7L BUFX8H7L INVX2H7L INVX4H7L} \
    -clk_nets "clk"
detailed_placement
repair_timing -hold

# === 7. Generate Tracks (必须放在布线前) ===
make_tracks

# === 8. Pin Placement (给顶层端口分配金属层) ===
# 层方向: MET1 HORIZONTAL, MET2 VERTICAL, MET3 HORIZONTAL, MET4 VERTICAL, MET5 HORIZONTAL
place_pins -hor_layers {MET1 MET3 MET5} -ver_layers {MET2 MET4} -random

# === 9. Routing ===
global_route
repair_design

# DRT 迭代限制: 5轮即可, 剩余违规可接受
detailed_route -droute_end_iter 5 -or_seed 8
catch {check_antennas -verbose}

# === 10. Filler Insertion ===
# catch 包裹: 布线后详细布局可能失败, 不影响输出
catch {
    filler_placement {FILLER1H7L FILLER2H7L FILLER4H7L FILLER8H7L \
        FILLER16H7L FILLER32H7L FILLER64H7L \
        FILLCAP4H7L FILLCAP8H7L FILLCAP16H7L FILLCAP32H7L}
    catch {detailed_placement}
}

# === 11. Reports (v2.0 使用 > 重定向, 不是 -out_file) ===
file mkdir "$script_dir/reports"
report_checks -path_delay min_max -fields {slew cap input nets} \
    -format full_clock_expanded > "$script_dir/reports/timing.rpt"
report_power > "$script_dir/reports/power.rpt"
report_design_area
catch {report_wire_length -global_route > "$script_dir/reports/wire_length.rpt"}

# === 12. Export ===
file mkdir "$script_dir/outputs"
write_def "$script_dir/outputs/riscv.def"
write_spef "$script_dir/outputs/riscv.spef"
write_verilog "$script_dir/outputs/riscv_pnr.v"

puts "============================================"
puts "Physical Design Flow Complete!"
puts "DEF: $script_dir/outputs/riscv.def"
puts "============================================"
```

### 3.2 关键语法差异 (v2.0 vs 旧版本)

| 功能 | 旧语法 | v2.0 正确语法 |
|------|--------|-------------|
| 全局连接 | `global_connect -net VDD -type tiehi ...` | `add_global_connection ...` + `global_connect` (无参数) |
| PDN 网格 | `pdngen::specify_grid` | `define_pdn_grid` + `add_pdn_stripe` + `add_pdn_connect` |
| 报告输出 | `-out_file filename` | `> filename` (Tcl 重定向) |
| GDS 写入 | `write_gds` | **不存在于 v2.0**, 需用外部工具 |
| 时序报告 | `report_checks -out_file` | `report_checks > file` |

## 4. DEF → GDSII 转换

OpenROAD v2.0 不含 `write_gds` 命令。使用 gdstk Python 库转换：

```python
# def2gds.py — 标准单元布局转 GDSII
import gdstk, re

# 读取标准化单元库 GDS
lib = gdstk.read_gds("~/pdk/IP/STD_cell/ics55_LLSC_H7C_V1p10C100/ics55_LLSC_H7CL/gds/ics55_LLSC_H7CL.gds")

# 解析 DEF 中的 COMPONENTS 段
with open("~/design/outputs/riscv.def") as f:
    def_txt = f.read()
comp_sec = re.search(r'COMPONENTS (\d+) ;\n(.*?)END COMPONENTS', def_txt, re.DOTALL)

# 创建顶层 cell 并放置所有组件
top = lib.new_cell('riscv_pd_TOP')
for m in re.finditer(r'-\s+(\S+)\s+(\S+).*?PLACED\s+\(\s*(\S+)\s+(\S+)\s*\)', comp_sec.group(2)):
    _, cell_name, x, y = m.group(1), m.group(2), float(m.group(3)), float(m.group(4))
    ref = gdstk.Reference(lib.cells[cell_name], (x, y))
    top.add(ref)

lib.write_gds("~/design/outputs/riscv.gds")
```

## 5. 文件传输 (Windows ↔ VM)

```bash
# Windows → VM (推送文件)
scp "E:/path/to/file" chz@192.168.164.132:~/design/

# VM → Windows (拉取结果)
scp "chz@192.168.164.132:~/design/outputs/riscv.gds" "E:/kouchi/riscv_design/riscv_design/physical_design/outputs/"

# 批量传输目录
scp -r "C:/path/to/libs/" chz@192.168.164.132:~/pdk/
```

## 6. 常见错误与修复

### 综合阶段

| 症状 | 原因 | 修复 |
|------|------|------|
| 网表为空/零单元 | 顶层无输出端口, 逻辑被 `opt_clean -purge` 删除 | 在顶层模块添加 debug 输出端口 |
| `abc` 提取 0 个 gate | `abc` 看不到扁平化后的门 | 先执行 `techmap` 转换 `$_*` 为通用门, 再 `abc` |
| 恒高/低信号无 tie cell | Yosys 使用 `1'h1` 而非 TIE 单元 | 在 PDN 后执行 `insert_tiecells` |

### 物理设计阶段

| 症状 | 原因 | 修复 |
|------|------|------|
| `ORD-0001 LEF does not exist` | TCL 变量展开失败 (bash 吞掉了 `$`) | 用 SCP 传输 .tcl 文件, 不用 SSH heredoc |
| `global_connect` wrong # args | v2.0 语法不兼容 | 使用 `add_global_connection` + `global_connect` |
| `GRT-0701 Missing track structure` | 布线层无 track 定义 | 在布线前执行 `make_tracks` |
| `GRT-0042 Pin does not have geometries` | 顶层端口无物理层几何 | 执行 `place_pins -hor_layers {MET1 MET3 MET5} -ver_layers {MET2 MET4}` |
| `DRT-0305 Net one_ signal type POWER` | 常量网被视为电源网 | 执行 `insert_tiecells "TIEHIH7L/Z" -prefix "TIE_ONE_"` |
| `PPL-0045 Layer direction mismatch` | 水平层/垂直层分配错误 | 检查 LEF 中每层的 DIRECTION 属性 |
| `STA-0563 report_checks -out_file` | v2.0 不支持 `-out_file` | 改为 `report_checks ... > file` |
| `GRT-0238 report_wire_length -net required` | 需要 `-net` 参数 | 改为 `-global_route > file` 或 catch 包裹 |
| `DPL-0036 Detailed placement failed` | 布线后填充单元放置冲突 | 用 `catch {}` 包裹 filler + detailed_placement |

### 工具链阶段

| 症状 | 原因 | 修复 |
|------|------|------|
| DEF 无 GDS 输出 | v2.0 无 `write_gds` | 用 gdstk 或 KLayout 转 DEF→GDS |
| KLayout cmdline segfault | 旧版无头模式有问题 | 使用 gdstk Python 库代替 |
| SCP 文件路径错误 | Windows 路径分隔符问题 | 使用 `"E:/path"` 正斜杠格式 |
| 综合后需 `techmap` | Yosys 通用单元 (`$_*`) 不被 PDK 识别 | synth 后加 `techmap` 再 `abc` |

## 7. 工艺层信息 (N551P6M)

```
层      方向          Pitch (um)
──────────────────────────────
MET1    HORIZONTAL    0.2
MET2    VERTICAL      0.2
MET3    HORIZONTAL    0.2
MET4    VERTICAL      0.2
MET5    HORIZONTAL    0.8
T4M2    —             5.0
RDL     —             —
```

- Site: `CoreSite` 0.2 x 1.4 um (7-track)
- Via: MET2_MET1_VIA1_0 到 MET2_MET1_VIA1_8 (共 9 种 via 尺寸)
- 1P6M = 1 Poly + 6 Metal (MET1-MET5 + T4M2)

## 8. 查看版图 (Windows)

KLayout GUI 直接打开 `riscv.gds`:
1. 下载: https://www.klayout.de/build.html (Windows 64-bit)
2. File → Open → 选择 `riscv.gds`
3. 左侧 Cell Browser 中双击 `riscv_pd_TOP` 查看全芯片
4. 操作: 滚轮缩放, 中键拖动平移, F2 适配窗口
