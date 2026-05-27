#!/usr/bin/env python3
"""
RISC-V RTL 逻辑综合脚本 —— 基于 Yosys + ICSprout55-PDK。

用法：
    python yosys_synth.py <rtl_files...> [--top TopModuleName] [--pdk-path /path/to/icsprout55]
        [--clk-period 10.0] [--output-dir synth_output] [--json] [--verbose]
        [--keep-hierarchy] [--no-memory-map]

ICSprout55-PDK:
    https://github.com/openecos-projects/icsprout55-pdk/
    首次使用请 git clone 到本地，然后用 --pdk-path 指定 PDK 根目录。

输出物（输出目录下）：
    - <top>_netlist.v    ：映射后的门级网表
    - <top>_stat.txt     ：面积/单元统计
    - <top>_timing.txt   ：时序报告（若 PDK 提供 .lib）
    - <top>.json         ：Yosys JSON 网表（若指定 --json）
    - yosys_log.txt      ：完整 Yosys 日志
    - pre_synth_check.txt: RTL 综合前检查报告
"""

import subprocess
import sys
import argparse
import shutil
import re
import json
from pathlib import Path
from datetime import datetime

YOSYS_EXECUTABLE = "yosys"

# ICSprout55-PDK 典型目录结构
EXPECTED_LIBERTY = "icsprout55_stdcell.lib"
EXPECTED_LIBERTY_GZ = "icsprout55_stdcell.lib.gz"


def find_yosys():
    """检查 Yosys 是否已安装"""
    path = shutil.which(YOSYS_EXECUTABLE)
    if path:
        return path
    # Windows 下常见安装路径
    for candidate in [
        r"C:\Program Files\Yosys\yosys.exe",
        r"C:\yosys\yosys.exe",
        r"C:\msys64\usr\local\bin\yosys.exe",
        r"C:\msys64\mingw64\bin\yosys.exe",
    ]:
        if Path(candidate).exists():
            return candidate
    return None


def auto_find_pdk():
    """自动搜索常见 PDK 安装位置，返回 (pdk_root, liberty_path)。

    优先选择标准单元库，标准单元中优先 ss (slow-slow) 工艺角。
    """
    candidates = [
        Path.home() / "icsprout55-pdk",
        Path.cwd() / "icsprout55-pdk",
        Path.cwd().parent / "icsprout55-pdk",
        Path("/opt/icsprout55-pdk"),
        Path("/usr/local/share/icsprout55-pdk"),
    ]
    for pdk in candidates:
        if pdk.exists():
            # 优先标准单元 + ss 工艺角
            lib = _pick_best_liberty(pdk)
            if lib:
                return pdk, lib
    return None, None


def _pick_best_liberty(pdk_root):
    """从 PDK 目录中选择最佳 liberty 文件。

    优先级：标准单元 > IO单元,  ss > tt > ff
    """
    all_libs = list(Path(pdk_root).rglob("*.lib"))
    all_libs += list(Path(pdk_root).rglob("*.lib.gz"))

    if not all_libs:
        return None

    std_libs = [l for l in all_libs if 'STD_cell' in str(l) or 'stdcell' in str(l).lower()]
    io_libs = [l for l in all_libs if 'IO/' in str(l) or 'io/' in str(l)]
    targets = std_libs if std_libs else (io_libs if io_libs else all_libs)

    # 选 ss（慢速/最差）工艺角 → 最保守的时序分析
    for keyword in ['ss_', 'ss.', '_ss_', 'slow', 'rcworst']:
        for lib in targets:
            if keyword in str(lib).lower():
                return str(lib)

    # 退而求其次
    for keyword in ['tt_', 'tt.', '_tt_', 'typ', 'typical']:
        for lib in targets:
            if keyword in str(lib).lower():
                return str(lib)

    return str(targets[0])


def find_liberty(pdk_root):
    """在 PDK 根目录下搜索 liberty 文件"""
    root = Path(pdk_root)
    if not root.exists():
        return None
    # 直接匹配
    for pattern in ["*.lib", "*.lib.gz", "**/*.lib", "**/*.lib.gz"]:
        found = list(root.glob(pattern))
        if found:
            return str(found[0])
    return None


def generate_yosys_script(top_module, rtl_files, liberty_path, clk_period_ns, output_dir, keep_hierarchy=False):
    """生成 Yosys TCL 综合脚本

    注意：Yosys TCL 语法与 shell 不同:
    - `>` 是 Yosys techmap 选择操作符，不能用于文件重定向
    - `echo` 仅支持 `echo on/off`，不支持打印字符串
    - 文件输出必须用 `tee -q -o <file> <command>`
    - MSYS2 Yosys 需要 Windows 风格路径 (C:/... 而非 /c/...)
    """
    output_dir = Path(output_dir)

    # 转换路径为 Yosys 兼容格式 (MSYS2 需要 Windows 路径)
    def _yosys_path(p):
        return str(p.resolve()).replace('\\', '/')

    script_lines = []

    # 读入 RTL（-nomeminit 跳过 $readmemh 避免综合报错）
    script_lines.append("# === 0. 读入 RTL ===")
    for f in rtl_files:
        script_lines.append(f"read_verilog -nomeminit -I {_yosys_path(Path(f).parent)} {_yosys_path(Path(f))}")
    script_lines.append(f"hierarchy -check -top {top_module}")
    script_lines.append("")

    # 工艺映射前的优化
    script_lines.append("# === 1. 工艺无关优化 ===")
    script_lines.append("proc")
    if not keep_hierarchy:
        script_lines.append("# 扁平化层次结构以进行跨模块优化")
        script_lines.append("flatten")
    else:
        script_lines.append("# 保留层次结构 (--keep-hierarchy)")
    script_lines.append("opt_expr")
    script_lines.append("opt_clean -purge")
    script_lines.append("check")
    script_lines.append("opt -nodffe -nosdff")
    script_lines.append("")

    # FSM 提取与优化
    script_lines.append("# === 2. FSM 提取与优化 ===")
    script_lines.append("fsm -expand")
    script_lines.append("opt")
    script_lines.append("")

    # 资源映射
    script_lines.append("# === 3. 资源映射 ===")
    script_lines.append("wreduce")
    script_lines.append("peepopt")
    script_lines.append("opt_clean -purge")
    script_lines.append("")

    # 存储器处理 — 保持为块 RAM，不展开为触发器海
    script_lines.append("# === 4. 存储器处理 ===")
    script_lines.append("memory -nomap")
    script_lines.append("memory_collect")
    script_lines.append("")

    # 技术映射前统计
    script_lines.append("# === 5. 映射前统计 ===")
    script_lines.append(f"tee -q -o {_yosys_path(output_dir / 'pre_map_stat.txt')} stat")
    script_lines.append("")

    # 工艺映射
    if liberty_path:
        lib_path = _yosys_path(Path(liberty_path))
        script_lines.append("# === 6. 工艺映射 (ICSprout55 55nm) ===")
        script_lines.append(f"read_liberty -lib {lib_path}")
        script_lines.append(f"dfflibmap -liberty {lib_path}")
        script_lines.append(f"abc -liberty {lib_path}")
        script_lines.append("")
    else:
        script_lines.append("# === 6. 工艺映射 (generic gates) ===")
        script_lines.append("techmap")
        script_lines.append("opt -fast")
        script_lines.append("abc")
        script_lines.append("")

    # 映射后清理
    script_lines.append("# === 7. 映射后优化 ===")
    script_lines.append("opt_clean -purge")
    script_lines.append("check")
    script_lines.append("")

    # 详细统计
    script_lines.append("# === 8. 面积与单元统计 ===")
    stat_base = f"stat -liberty {_yosys_path(Path(liberty_path))}" if liberty_path else "stat"
    script_lines.append(f"tee -q -o {_yosys_path(output_dir / 'yosys_stat.txt')} {stat_base}")
    script_lines.append(f"tee -q -o {_yosys_path(output_dir / 'stat.txt')} {stat_base}")
    script_lines.append(f"tee -q -o {_yosys_path(output_dir / 'stat_width.txt')} stat -width")
    script_lines.append("")

    # 层次化统计
    if keep_hierarchy:
        script_lines.append("# === 9. 逐模块统计 ===")
        script_lines.append("stat")

    # 网表输出
    script_lines.append("# === 10. 输出网表 ===")
    script_lines.append(f"write_verilog -noexpr -noattr {_yosys_path(output_dir / f'{top_module}_netlist.v')}")
    script_lines.append("")

    return "\n".join(script_lines)


def run_yosys(yosys_bin, script_path, output_dir):
    """执行 Yosys"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "yosys_log.txt"

    cmd = [yosys_bin, "-s", str(script_path), "-q" if not _verbose else ""]
    cmd = [c for c in cmd if c]  # 过滤空字符串

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 分钟超时
        )
        log_path.write_text(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            print(f"[警告] Yosys 返回非零退出码 {result.returncode}")
            print(f"  完整日志: {log_path}")
        return result.returncode, log_path
    except subprocess.TimeoutExpired:
        print("[错误] Yosys 运行超过 5 分钟，已超时。")
        return -1, None
    except OSError as e:
        print(f"[错误] 无法启动 Yosys: {e}")
        return -1, None


def pre_synth_check(rtl_files):
    """综合前 RTL 检查：扫描常见综合陷阱。

    返回 (warnings, errors) 的元组。
    """
    import re
    warnings = []
    errors = []

    SYSTEM_TASK_RE = re.compile(r'\$(display|monitor|write|strobe|error|warning|fatal|readmemh|readmemb|writememh|writememb)')
    INITIAL_BLOCK_RE = re.compile(r'^\s*initial\b', re.MULTILINE)
    TRANSLATE_OFF_GUARD = re.compile(r'//\s*synthesis\s*translate_off')

    for f in rtl_files:
        try:
            content = Path(f).read_text(errors='ignore')
        except Exception:
            continue

        # 检查系统任务 ($display/$readmemh 等)
        task_matches = SYSTEM_TASK_RE.findall(content)
        if task_matches:
            # 检查是否已被 translate_off 包裹
            if not TRANSLATE_OFF_GUARD.search(content):
                unique_tasks = set(task_matches)
                warnings.append(
                    f"{f}: 发现系统任务 {unique_tasks} — 综合时可能报错或产生垃圾单元。"
                    f" 建议用 // synthesis translate_off / // synthesis translate_on 包裹。"
                )

        # 检查 initial 块 (非 testbench 的 RTL)
        if Path(f).suffix == '.v' and 'tb' not in Path(f).stem.lower():
            if INITIAL_BLOCK_RE.search(content) and not TRANSLATE_OFF_GUARD.search(content):
                warnings.append(
                    f"{f}: 发现 initial 块 — Yosys 综合会忽略 (可能导致 ROM/RAM 未初始化)。"
                )

    return warnings, errors


def parse_stat(stat_path):
    """解析 Yosys stat 输出，提取关键指标。

    支持扁平化和层次化两种输出格式。
    对于层次化输出，统计 design hierarchy 部分的汇总数据。
    """
    if not stat_path.exists():
        return None
    text = stat_path.read_text()
    info = {}

    # 优先匹配 design hierarchy 的汇总行
    dh_section = re.search(r'=== design hierarchy ===\s*\n\s*\n.*?\n(.*?)(?:\n\s*\n|$)', text, re.DOTALL)
    if dh_section:
        section_text = dh_section.group(1)
    else:
        section_text = text

    # 匹配单元数
    m = re.search(r"Number of cells:\s*(\d+)", section_text)
    if m:
        info["cell_count"] = int(m.group(1))

    # 匹配面积估算
    m = re.search(r"Chip area for module.*?:\s*([0-9.]+)", text)
    if m:
        info["area"] = float(m.group(1))

    # 匹配 wire 数
    m = re.search(r"Number of wires:\s*(\d+)", section_text)
    if m:
        info["wire_count"] = int(m.group(1))

    # 匹配存储器位数
    m = re.search(r"Number of memory bits:\s*(\d+)", section_text)
    if m:
        info["memory_bits"] = int(m.group(1))

    # 匹配各类型单元数
    for cell_type in re.finditer(r"Number of (\w+) cells:\s*(\d+)", section_text):
        info[f"{cell_type.group(1)}_cells"] = int(cell_type.group(2))

    return info


def main():
    global _verbose

    parser = argparse.ArgumentParser(
        description="RISC-V RTL 逻辑综合 —— Yosys + ICSprout55-PDK"
    )
    parser.add_argument("rtl_files", nargs="+", help="RTL 源文件（.v / .sv）")
    parser.add_argument("--top", required=True, help="顶层模块名")
    parser.add_argument("--pdk-path", default=None, help="ICSprout55-PDK 根目录路径")
    parser.add_argument("--clk-period", type=float, default=10.0, help="时钟周期，单位 ns（默认 10）")
    parser.add_argument("--output-dir", "-o", default="synth_output", help="输出目录")
    parser.add_argument("--json", action="store_true", help="额外输出 Yosys JSON 网表")
    parser.add_argument("--keep-hierarchy", action="store_true",
                        help="保留模块层次结构，不执行 flatten（含大量存储器的设计推荐启用）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--remote", action="store_true",
                        help="通过 SSH 在远程 VM 上运行 Yosys（推荐，需先配置 VM 连接）")
    parser.add_argument("--vm-host", default=None, help="VM 主机名/IP（覆盖环境变量 RISCV_VM_HOST）")
    parser.add_argument("--vm-user", default=None, help="VM SSH 用户名（覆盖环境变量 RISCV_VM_USER）")
    parser.add_argument("--vm-port", type=int, default=None, help="VM SSH 端口（覆盖环境变量 RISCV_VM_PORT）")
    parser.add_argument("--vm-key", default=None, help="SSH 私钥路径（覆盖环境变量 RISCV_VM_KEY）")
    parser.add_argument("--vm-work-dir", default=None, help="VM 远程工作目录（覆盖环境变量 RISCV_VM_WORK_DIR）")
    parser.add_argument("--no-fallback", action="store_true",
                        help="远程综合失败时不回退到本地 Yosys")
    args = parser.parse_args()
    _verbose = args.verbose

    # === 远程综合路径 ===
    if args.remote:
        try:
            from vm_runner import (get_vm_config, check_ssh_connection,
                                   remote_yosys_synth, scp_upload, ssh_run,
                                   create_remote_temp_dir)
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
            if args.no_fallback:
                sys.exit(1)
            print("[信息] 回退到本地 Yosys ...\n")
        else:
            # 解析 liberty 路径（本地 PDK 路径 → 上传到 VM）
            liberty_local = None
            if args.pdk_path:
                liberty_local = _pick_best_liberty(Path(args.pdk_path))
                if liberty_local:
                    print(f"[信息] 找到 liberty 文件: {liberty_local}")
            else:
                auto_pdk, auto_lib = auto_find_pdk()
                if auto_pdk:
                    liberty_local = auto_lib
                    print(f"[信息] 自动发现 PDK: {auto_pdk}")

            # 生成综合脚本（使用本地路径，vm_runner 会替换为远程路径）
            output_dir = Path(args.output_dir)
            script_content = generate_yosys_script(
                args.top, args.rtl_files, liberty_local,
                args.clk_period, output_dir,
                keep_hierarchy=args.keep_hierarchy,
            )

            # 上传 liberty 文件（如有）
            if liberty_local:
                remote_dir = create_remote_temp_dir(config)
                if remote_dir:
                    remote_pdk_dir = f"{remote_dir}/pdk"
                    ssh_run(config, f"mkdir -p {remote_pdk_dir}", timeout=10, verbose=False)
                    scp_upload(config, liberty_local, f"{remote_pdk_dir}/")
                    # 替换脚本中的本地 liberty 路径为远程路径
                    remote_liberty = f"{remote_pdk_dir}/{Path(liberty_local).name}"
                    script_content = script_content.replace(
                        str(Path(liberty_local).resolve()).replace('\\', '/'), remote_liberty
                    )
                    script_content = script_content.replace(
                        str(liberty_local), remote_liberty
                    )

            retcode, _ = remote_yosys_synth(
                config, args.rtl_files, script_content,
                args.output_dir, verbose=args.verbose,
            )
            if retcode == 0:
                print("\n[信息] 远程综合完成。")
                print(f"  结果目录: {args.output_dir}")
                print(f"  运行 eval_circuit.py {args.output_dir} 进行电路评估。")
                sys.exit(0)
            else:
                print(f"\n[警告] 远程综合失败 (exit={retcode})")
                if not args.no_fallback:
                    print("[信息] 回退到本地 Yosys ...\n")
                else:
                    sys.exit(retcode)

    # === 本地综合路径 ===
    # 1. 检查 Yosys
    yosys_bin = find_yosys()
    if not yosys_bin:
        print("=" * 60)
        print("[错误] 未找到 Yosys。")
        print("  安装: https://github.com/YosysHQ/yosys")
        print("  Windows: 可通过 MSYS2 安装: pacman -S mingw-w64-x86_64-yosys")
        print("  Linux:   apt install yosys / brew install yosys")
        print("  或使用 --remote 在 VM 上运行综合。")
        print("=" * 60)
        sys.exit(1)
    print(f"[信息] Yosys: {yosys_bin}")

    # 2. 综合前 RTL 检查
    print("[信息] 综合前 RTL 检查 ...")
    warnings, errors = pre_synth_check(args.rtl_files)
    if warnings:
        print(f"\n  发现 {len(warnings)} 个潜在的综合问题:")
        for w in warnings:
            print(f"  ⚠ {w}")
    if errors:
        print(f"\n  发现 {len(errors)} 个错误:")
        for e in errors:
            print(f"  ❌ {e}")
    if not warnings and not errors:
        print("  未发现明显问题。")
    print()

    # 3. 检查 RTL 文件
    for f in args.rtl_files:
        if not Path(f).exists():
            print(f"[错误] RTL 文件不存在: {f}")
            sys.exit(1)

    # 4. 查找 PDK / liberty
    liberty_path = None
    if args.pdk_path:
        liberty_path = _pick_best_liberty(Path(args.pdk_path))
        if liberty_path:
            print(f"[信息] 找到 liberty 文件: {liberty_path}")
        else:
            print(f"[警告] 在 {args.pdk_path} 中未找到 .lib 文件")
    else:
        # 自动搜索常见位置
        auto_pdk, auto_lib = auto_find_pdk()
        if auto_pdk:
            liberty_path = auto_lib
            print(f"[信息] 自动发现 PDK: {auto_pdk}")
            print(f"[信息] 选中工艺角: {Path(auto_lib).name}")
        else:
            print("[信息] 未指定 --pdk-path 且未自动发现 PDK，使用 generic 映射")
            print("  下载 PDK: git clone https://github.com/openecos-projects/icsprout55-pdk.git ~/icsprout55-pdk")

    # 5. 生成 Yosys 脚本
    output_dir = Path(args.output_dir)
    script_path = output_dir / "synth.ys"
    script_content = generate_yosys_script(
        args.top, args.rtl_files, liberty_path, args.clk_period, output_dir,
        keep_hierarchy=args.keep_hierarchy
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script_content)

    if args.verbose:
        print("\n--- Yosys 综合脚本 ---")
        print(script_content)
        print("--- 脚本结束 ---\n")

    # 6. 运行 Yosys
    print(f"\n[信息] 正在综合顶层模块 '{args.top}' ...")
    print(f"  RTL 文件: {', '.join(args.rtl_files)}")
    print(f"  输出目录: {output_dir}")
    print()

    retcode, log_path = run_yosys(yosys_bin, script_path, output_dir)

    # 7. 解析结果
    stat_path = output_dir / "stat.txt"
    if stat_path.exists():
        info = parse_stat(stat_path)
        if info:
            print("\n" + "=" * 50)
            print(" 综合结果统计")
            print("=" * 50)
            cell_count = info.get("cell_count", "N/A")
            area = info.get("area", "N/A")
            wires = info.get("wire_count", "N/A")
            mem_bits = info.get("memory_bits", "N/A")
            print(f"  门级单元数      : {cell_count}")
            print(f"  Wire 数         : {wires}")
            print(f"  存储器位数      : {mem_bits}")
            if area != "N/A":
                print(f"  面积估算        : {area} (单位取决于 PDK)")
            # 分类型统计
            for k, v in sorted(info.items()):
                if k.endswith("_cells") and v > 0:
                    print(f"    - {k.replace('_cells', '')}: {v}")
            print(f"\n  完整日志: {log_path}")
            print(f"  网表文件: {output_dir / f'{args.top}_netlist.v'}")

    # 8. 可选 JSON 输出
    if args.json:
        json_path = output_dir / f"{args.top}.json"
        json_script = output_dir / "write_json.ys"
        json_script.write_text(
            "\n".join([f"read_verilog -nomeminit {f}" for f in args.rtl_files]
                      + [f"hierarchy -top {args.top}", "proc", f"write_json {json_path}"])
        )
        subprocess.run(
            [yosys_bin, "-s", str(json_script), "-q"],
            capture_output=True, timeout=60,
        )
        if json_path.exists():
            print(f"  JSON 网表: {json_path}")

    sys.exit(retcode if retcode is not None and retcode >= 0 else 1)


if __name__ == "__main__":
    main()
