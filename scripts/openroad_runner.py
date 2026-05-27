#!/usr/bin/env python3
"""
OpenROAD 物理设计自动化脚本 —— 网表 → GDSII。

基于 ICSprout55 55nm (1P6M) 工艺，ics55_LLSC_H7CL 标准单元库。

用法:
  # 生成脚本（本地模式，不执行）
  python openroad_runner.py synth_output/riscv_pd_netlist.v \\
      --top riscv_pd --pdk-path ~/icsprout55-pdk \\
      --clk-period 5.0 -o physical_design/

  # 远程自动流程（通过 SSH 在 VM 上运行 OpenROAD）
  python openroad_runner.py synth_output/riscv_pd_netlist.v \\
      --top riscv_pd --pdk-path ~/icsprout55-pdk \\
      --clk-period 5.0 --remote -o physical_design/
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime


def find_pdk_files(pdk_root):
    """在 PDK 目录中定位关键文件。

    返回 (tech_lef, std_lef, std_liberty, std_gds)。
    """
    pdk = Path(pdk_root)
    tech_lef = None
    std_lef = None
    std_liberty = None
    std_gds = None

    for f in pdk.rglob("*.lef"):
        f_str = str(f)
        if "techLEF" in f_str or "N551P6M" in f_str:
            tech_lef = f
        elif "ant" not in f_str.lower():
            std_lef = f

    for f in pdk.rglob("*.lib"):
        f_str = str(f)
        if "ss_rcworst" in f_str or "ss_" in f_str or "slow" in f_str.lower():
            std_liberty = f
            break
    if not std_liberty:
        libs = list(pdk.rglob("*.lib"))
        if libs:
            std_liberty = libs[0]

    for f in pdk.rglob("*.gds"):
        std_gds = f
        break

    return tech_lef, std_lef, std_liberty, std_gds


def generate_openroad_tcl(remote_dir, remote_pdk, tech_lef_name, std_lef_name,
                          liberty_name, netlist_name, top_module, clk_period_ns,
                          ant_lef_name=None):
    """生成 OpenROAD v2.0 物理设计 TCL 脚本。

    基于 physical-design.md 中的已验证模板。
    """
    ant_lef_block = ""
    if ant_lef_name:
        ant_lef_block = f'if {{[file exists "$pdk_dir/{ant_lef_name}"]}} {{\n    read_lef "$pdk_dir/{ant_lef_name}"\n}}'

    return f'''# =============================================================================
# OpenROAD Physical Design Flow — ICSprout55 55nm (1P6M)
# 自动生成于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
# 目标: {top_module}, 时钟周期: {clk_period_ns}ns
# =============================================================================

set script_dir "{remote_dir}"
set pdk_dir "{remote_pdk}"

# === 1. Setup ===
read_lef "$pdk_dir/{tech_lef_name}"
read_lef "$pdk_dir/{std_lef_name}"
{ant_lef_block}
read_liberty "$pdk_dir/{liberty_name}"
read_verilog "$script_dir/{netlist_name}"
link_design {top_module}

# === 2. Floorplan ===
initialize_floorplan \\
    -site CoreSite \\
    -utilization 60 \\
    -aspect_ratio 1.0 \\
    -core_space {{10 10 10 10}}

# === 3. Power Delivery Network ===
add_global_connection -net VDD -inst_pattern {{.*}} -pin_pattern {{^VDD$}} -power
add_global_connection -net VSS -inst_pattern {{.*}} -pin_pattern {{^VSS$}} -ground
global_connect

set_voltage_domain -name CORE -power VDD -ground VSS
define_pdn_grid -name grid -voltage_domains CORE
add_pdn_stripe -grid grid -layer MET1 -width 0.09 -followpins -extend_to_core_ring
add_pdn_stripe -grid grid -layer MET4 -width 1.0 -pitch 10.0 -offset 0 -extend_to_core_ring
add_pdn_stripe -grid grid -layer MET5 -width 2.0 -pitch 20.0 -offset 0 -extend_to_core_ring
add_pdn_connect -grid grid -layers {{MET1 MET4}}
add_pdn_connect -grid grid -layers {{MET4 MET5}}
pdngen

insert_tiecells "TIEHIH7L/Z" -prefix "TIE_ONE_"
insert_tiecells "TIELOH7L/Z" -prefix "TIE_ZERO_"

# === 4. SDC 约束 ===
set sdc_fh [open "$script_dir/riscv.sdc" w]
puts $sdc_fh {{
create_clock -name clk -period {clk_period_ns} [get_ports clk]
set_propagated_clock clk
set_input_delay -clock clk -max 0.5 [get_ports rst]
set_output_delay -clock clk -max 0.5 [all_outputs]
set_clock_uncertainty 0.1 clk
set_load 0.05 [all_outputs]
}}
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

clock_tree_synthesis \\
    -buf_list {{BUFX2H7L BUFX4H7L BUFX8H7L INVX2H7L INVX4H7L}} \\
    -clk_nets "clk"
detailed_placement
repair_timing -hold

# === 7. Generate Tracks ===
make_tracks

# === 8. Pin Placement ===
place_pins -hor_layers {{MET1 MET3 MET5}} -ver_layers {{MET2 MET4}} -random

# === 9. Routing ===
global_route
repair_design
detailed_route -droute_end_iter 5 -or_seed 8
catch {{check_antennas -verbose}}

# === 10. Filler Insertion ===
catch {{
    filler_placement {{FILLER1H7L FILLER2H7L FILLER4H7L FILLER8H7L \\
        FILLER16H7L FILLER32H7L FILLER64H7L \\
        FILLCAP4H7L FILLCAP8H7L FILLCAP16H7L FILLCAP32H7L}}
    catch {{detailed_placement}}
}}

# === 11. Reports ===
file mkdir "$script_dir/reports"
report_checks -path_delay min_max -fields {{slew cap input nets}} \\
    -format full_clock_expanded > "$script_dir/reports/timing.rpt"
report_power > "$script_dir/reports/power.rpt"
report_design_area
catch {{report_wire_length -global_route > "$script_dir/reports/wire_length.rpt"}}

# === 12. Export ===
file mkdir "$script_dir/outputs"
write_def "$script_dir/outputs/riscv.def"
write_spef "$script_dir/outputs/riscv.spef"
write_verilog "$script_dir/outputs/riscv_pnr.v"

puts "============================================"
puts "Physical Design Flow Complete!"
puts "DEF: $script_dir/outputs/riscv.def"
puts "============================================"
'''


def generate_def2gds_script(gds_path, def_path, output_gds, top_cell_name="riscv_pd_TOP"):
    """生成 gdstk DEF → GDSII 转换脚本。"""
    return f'''#!/usr/bin/env python3
"""DEF → GDSII 转换 —— 基于 gdstk 库。

用法: python def2gds.py
输出: {output_gds}
"""
import gdstk, re

# 读取标准单元库 GDS
lib = gdstk.read_gds("{gds_path}")
print(f"已加载 GDS 库: {{len(lib.cells)}} 个单元")

# 解析 DEF 中的 COMPONENTS 段
with open("{def_path}") as f:
    def_txt = f.read()
comp_sec = re.search(r'COMPONENTS (\\d+) ;\\n(.*?)END COMPONENTS', def_txt, re.DOTALL)
if not comp_sec:
    print("[错误] 未找到 COMPONENTS 段")
    exit(1)

# 创建顶层 cell
top = gdstk.Cell("{top_cell_name}")
lib.add(top)

count = 0
for m in re.finditer(
    r'-\\s+(\\S+)\\s+(\\S+).*?PLACED\\s+\\(\\s*(\\S+)\\s+(\\S+)\\s*\\)',
    comp_sec.group(2)
):
    _, cell_name, x, y = m.group(1), m.group(2), float(m.group(3)), float(m.group(4))
    if cell_name in lib.cells:
        ref = gdstk.Reference(lib.cells[cell_name], (x, y))
        top.add(ref)
        count += 1
    else:
        print(f"[警告] 单元 {{cell_name}} 不在 GDS 库中")

print(f"已放置 {{count}} 个单元")
lib.write_gds("{output_gds}")
print(f"GDSII 已写入: {output_gds}")
'''


def main():
    parser = argparse.ArgumentParser(
        description="OpenROAD 物理设计自动化 —— 网表 → GDSII"
    )
    parser.add_argument("netlist", help="综合后的门级网表 (.v)")
    parser.add_argument("--top", required=True, help="顶层模块名")
    parser.add_argument("--pdk-path", required=True, help="ICSprout55-PDK 根目录")
    parser.add_argument("--clk-period", type=float, default=5.0, help="时钟周期 ns（默认 5.0）")
    parser.add_argument("--output-dir", "-o", default="physical_design", help="输出目录")
    parser.add_argument("--remote", action="store_true",
                        help="通过 SSH 在远程 VM 上运行 OpenROAD")
    parser.add_argument("--vm-host", default=None, help="VM 主机名/IP")
    parser.add_argument("--vm-user", default=None, help="VM SSH 用户名")
    parser.add_argument("--vm-port", type=int, default=None, help="VM SSH 端口")
    parser.add_argument("--vm-key", default=None, help="SSH 私钥路径")
    parser.add_argument("--vm-work-dir", default=None, help="VM 远程工作目录")
    args = parser.parse_args()

    netlist_path = Path(args.netlist)
    if not netlist_path.exists():
        print(f"[错误] 网表文件不存在: {args.netlist}")
        sys.exit(1)

    pdk_path = Path(args.pdk_path)
    if not pdk_path.exists():
        print(f"[错误] PDK 目录不存在: {args.pdk_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 查找 PDK 文件
    print("[信息] 搜索 PDK 文件 ...")
    tech_lef, std_lef, std_liberty, std_gds = find_pdk_files(pdk_path)

    if not tech_lef:
        print("[警告] 未找到工艺 LEF (techLEF)")
    else:
        print(f"  工艺 LEF: {tech_lef}")
    if not std_lef:
        print("[警告] 未找到标准单元 LEF")
    else:
        print(f"  标准单元 LEF: {std_lef}")
    if not std_liberty:
        print("[错误] 未找到 liberty 文件 (.lib)，无法继续")
        sys.exit(1)
    else:
        print(f"  Liberty: {std_liberty}")
    if std_gds:
        print(f"  GDS: {std_gds}")

    # 2. 生成 OpenROAD TCL 脚本
    print("\n[信息] 生成 OpenROAD TCL 脚本 ...")
    remote_dir = "/tmp/openroad_work"  # 占位符，远程模式会被替换
    remote_pdk = "/tmp/openroad_work/pdk"

    tech_lef_name = tech_lef.name if tech_lef else "N551P6M.lef"
    std_lef_name = std_lef.name if std_lef else "ics55_LLSC_H7CL.lef"
    liberty_name = std_liberty.name if std_liberty else "stdcell.lib"
    ant_lef_name = None
    if std_lef:
        ant_candidate = std_lef.parent / f"{std_lef.stem}_ant.lef"
        if ant_candidate.exists():
            ant_lef_name = ant_candidate.name

    tcl_script = generate_openroad_tcl(
        remote_dir, remote_pdk,
        tech_lef_name, std_lef_name, liberty_name,
        netlist_path.name, args.top, args.clk_period,
        ant_lef_name=ant_lef_name,
    )

    tcl_path = output_dir / "run_pd.tcl"
    tcl_path.write_text(tcl_script)
    print(f"  TCL 脚本: {tcl_path}")

    # 3. 生成 DEF→GDS 转换脚本
    if std_gds:
        print("[信息] 生成 DEF→GDS 转换脚本 ...")
        def2gds_script = generate_def2gds_script(
            str(std_gds),
            f"{output_dir}/riscv.def",
            f"{output_dir}/riscv.gds",
        )
        def2gds_path = output_dir / "def2gds.py"
        def2gds_path.write_text(def2gds_script)
        print(f"  def2gds 脚本: {def2gds_path}")

    # 4. 远程或本地
    if args.remote:
        print("\n" + "=" * 60)
        print(" 远程物理设计模式")
        print("=" * 60)

        try:
            from vm_runner import (get_vm_config, check_ssh_connection,
                                   remote_openroad_flow, scp_upload, ssh_run)
        except ImportError:
            print("[错误] 无法导入 vm_runner 模块，请确认 scripts/vm_runner.py 存在。")
            sys.exit(1)

        config = get_vm_config(
            vm_host=args.vm_host,
            vm_user=args.vm_user,
            vm_port=args.vm_port,
            vm_key=args.vm_key,
            vm_work_dir=args.vm_work_dir,
        )
        if config.get("_error"):
            print(config["_message"])
            sys.exit(1)

        ok, msg = check_ssh_connection(config)
        if not ok:
            print(f"[错误] 无法连接到 VM: {msg}")
            sys.exit(1)

        # 升级 TCL 脚本中的路径为实际远程路径
        # remote_openroad_flow 会创建临时目录，我们这里先用占位符
        # 实际的路径替换在 remote_openroad_flow 内部完成

        # 收集 PDK 上传列表
        pdk_uploads = []
        if tech_lef:
            pdk_uploads.append(("工艺 LEF", tech_lef))
        if std_lef:
            pdk_uploads.append(("标准单元 LEF", std_lef))
        if std_lef and ant_lef_name:
            ant_lef = std_lef.parent / ant_lef_name
            if ant_lef.exists():
                pdk_uploads.append(("天线 LEF", ant_lef))
        if std_liberty:
            pdk_uploads.append(("Liberty", std_liberty))

        retcode, _ = remote_openroad_flow(
            config, args.netlist, pdk_uploads, tcl_script,
            args.output_dir,
        )

        if retcode == 0:
            print("\n[信息] 远程物理设计完成！")
            print(f"  结果目录: {args.output_dir}")
            if std_gds:
                print(f"\n  下一步: 运行 def2gds.py 生成 GDSII")
                print(f"    python {output_dir / 'def2gds.py'}")
                print(f"  或直接用 KLayout 打开 DEF 查看布局。")
        else:
            print(f"\n[错误] 物理设计失败 (exit={retcode})")
            print(f"  查看日志: {output_dir / 'openroad_log.txt'}")
            sys.exit(retcode)

    else:
        # 本地模式：仅生成脚本
        print("\n" + "=" * 60)
        print(" 本地模式：已生成脚本，需手动执行")
        print("=" * 60)
        print(f"""
  在 Linux VM 上执行:
    1. SCP 传输文件到 VM:
       scp {args.netlist} <username>@<VM_IP>:~/design/
       scp {tcl_path} <username>@<VM_IP>:~/design/
       scp -r {args.pdk_path}/* <username>@<VM_IP>:~/pdk/

    2. 修改 {tcl_path.name} 中的路径:
       set script_dir "~/design"
       set pdk_dir "~/pdk/..."

    3. 在 VM 上运行:
       openroad -no_init -exit run_pd.tcl

    4. 下载结果:
       scp -r <username>@<VM_IP>:~/design/outputs/ {output_dir}/
       scp -r <username>@<VM_IP>:~/design/reports/ {output_dir}/

  或使用 --remote 一键自动执行。
""")
        print(f"  已生成文件:")
        print(f"    {tcl_path}")
        if std_gds:
            print(f"    {def2gds_path}")


if __name__ == "__main__":
    main()
