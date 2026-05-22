#!/usr/bin/env python3
"""
RISC-V 电路评估脚本 —— 解析综合/仿真结果并生成 QoR 报告。

用法：
    python eval_circuit.py <synth_output_dir>
    python eval_circuit.py <synth_output_dir> --constraints <target_freq_mhz> <area_budget_um2>
    python eval_circuit.py <synth_output_dir> --compare <other_output_dir>  # 对比两次综合

评估维度：
    - 面积 (cell count / area estimate)
    - 时序 (critical path / max frequency)
    - 单元分布 (组合 vs 时序逻辑)
    - 违规项 (lint 检查结果)
    - QoR 评分 (Quality of Results, 0-100)
"""

import re
import sys
import argparse
from pathlib import Path
from datetime import datetime


def parse_yosys_stat(path):
    """解析 Yosys stat 输出"""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    info = {}

    m = re.search(r"Number of cells:\s*(\d+)", text)
    if m:
        info["cell_count"] = int(m.group(1))

    m = re.search(r"Chip area for module.*?:\s*([0-9.]+)", text)
    if m:
        info["area"] = float(m.group(1))

    m = re.search(r"Number of wires:\s*(\d+)", text)
    if m:
        info["wire_count"] = int(m.group(1))

    m = re.search(r"Number of public wires:\s*(\d+)", text)
    if m:
        info["public_wire_count"] = int(m.group(1))

    m = re.search(r"Number of memories:\s*(\d+)", text)
    if m:
        info["memory_count"] = int(m.group(1))

    for cell_type in re.finditer(r"Number of (\w+) cells:\s*(\d+)", text):
        info[f"{cell_type.group(1).lower()}_cells"] = int(cell_type.group(2))

    return info


def parse_yosys_log(log_path):
    """从 Yosys 日志中提取时序和警告信息"""
    if not log_path.exists():
        return {}
    text = log_path.read_text(encoding="utf-8", errors="replace")
    info = {}

    # 统计警告数
    warnings = re.findall(r"Warning:", text, re.IGNORECASE)
    info["yosys_warnings"] = len(warnings)

    # 统计错误数
    errors = re.findall(r"ERROR", text)
    info["yosys_errors"] = len(errors)

    # 尝试提取 ABC 映射信息
    m = re.search(r"ABC:.*?area\s*[=:]\s*([0-9.]+)", text)
    if m:
        info["abc_area"] = float(m.group(1))

    m = re.search(r"ABC:.*?delay\s*[=:]\s*([0-9.]+)", text)
    if m:
        info["abc_delay"] = float(m.group(1))

    return info


def parse_lint_results(lint_log_path):
    """解析 lint 日志中的 ERROR/WARNING/STYLE 计数"""
    if not lint_log_path or not Path(lint_log_path).exists():
        return {}
    text = Path(lint_log_path).read_text(encoding="utf-8", errors="replace")
    info = {}
    m = re.search(r"摘要.*?(\d+)\s*个错误.*?(\d+)\s*个警告.*?(\d+)\s*个风格问题.*?(\d+)\s*条信息", text)
    if m:
        info["lint_errors"] = int(m.group(1))
        info["lint_warnings"] = int(m.group(2))
        info["lint_style"] = int(m.group(3))
        info["lint_info"] = int(m.group(4))
    return info


def score_qor(stat, log, constraints):
    """
    计算 QoR 评分（0-100）。
    加权：面积 40% / 时序 30% / 警告 15% / 单元多样性 15%
    """
    score = 100.0
    details = []

    cell_count = stat.get("cell_count", 0)
    if cell_count == 0:
        return 0, ["无有效单元统计，无法评分。"]

    # 1. 面积评分 (40)
    area = stat.get("area", None)
    if area is not None and constraints.get("area_budget"):
        budget = constraints["area_budget"]
        if area <= budget:
            area_score = 40.0
            details.append(f"✅ 面积 {area:.1f} ≤ 预算 {budget:.1f} (+40)")
        else:
            ratio = budget / area
            area_score = max(0, 40.0 * ratio)
            details.append(f"⚠️ 面积 {area:.1f} > 预算 {budget:.1f} (ratio={ratio:.2f}, +{area_score:.1f})")
        score -= (40.0 - area_score)
    else:
        # 无面积约束时基于单元数给基准分
        if cell_count < 500:
            area_score = 40.0
        elif cell_count < 5000:
            area_score = 35.0
        elif cell_count < 50000:
            area_score = 30.0
        else:
            area_score = 25.0
        details.append(f"📐 {cell_count} 个单元 → 面积评分 +{area_score:.1f}/40")
        score -= (40.0 - area_score)

    # 2. 时序评分 (30)
    freq_mhz = constraints.get("target_freq_mhz", 100)
    abc_delay = log.get("abc_delay", None)
    if abc_delay is not None and abc_delay > 0:
        max_freq = 1000.0 / abc_delay  # delay 通常以 ps 为单位
        if max_freq >= freq_mhz:
            timing_score = 30.0
            details.append(f"✅ 最大频率 {max_freq:.0f} MHz ≥ 目标 {freq_mhz} MHz (+30)")
        else:
            ratio = max_freq / freq_mhz
            timing_score = max(0, 30.0 * ratio)
            details.append(f"⚠️ 最大频率 {max_freq:.0f} MHz < 目标 {freq_mhz} MHz (+{timing_score:.1f})")
        score -= (30.0 - timing_score)
    else:
        details.append("📊 时序信息不可用（需要 PDK .lib）→ 跳过时序评分")
        score -= 20  # 缺失时序信息扣 20 分

    # 3. 警告评分 (15)
    warnings = log.get("yosys_warnings", 0)
    if warnings == 0:
        warn_score = 15.0
        details.append("✅ 0 条 Yosys 警告 (+15)")
    elif warnings <= 10:
        warn_score = 12.0
        details.append(f"⚡ {warnings} 条 Yosys 警告 (+12)")
    elif warnings <= 50:
        warn_score = 8.0
        details.append(f"⚠️ {warnings} 条 Yosys 警告 (+8)")
    else:
        warn_score = max(0, 15.0 - warnings * 0.5)
        details.append(f"🔴 {warnings} 条 Yosys 警告 (+{warn_score:.1f})")
    score -= (15.0 - warn_score)

    # 4. 单元多样性评分 (15) — 有寄存器 + 组合逻辑 = 设计更完整
    comb_cells = 0
    seq_cells = 0
    for k, v in stat.items():
        if k.endswith("_cells"):
            if any(c in k for c in ["dff", "dffsr", "dlatch", "adff", "sdff"]):
                seq_cells += v
            else:
                comb_cells += v
    if seq_cells > 0 and comb_cells > 0:
        div_score = 15.0
        details.append(f"✅ 组合 {comb_cells} + 时序 {seq_cells} 单元，结构完整 (+15)")
    elif seq_cells > 0 or comb_cells > 0:
        div_score = 8.0
        details.append(f"⚡ 仅有 {'时序' if seq_cells > 0 else '组合'} 单元 (+8)")
    else:
        div_score = 0
        details.append("🔴 无有效单元分类 (+0)")
    score -= (15.0 - div_score)

    return max(0, score), details


def evaluate(synth_dir, constraints=None, compare_dir=None):
    """执行完整电路评估"""
    synth_dir = Path(synth_dir)
    constraints = constraints or {}

    print("\n" + "=" * 60)
    print("  RISC-V 电路评估报告")
    print("  时间:", datetime.now().isoformat())
    print("=" * 60)

    # 解析综合输出
    stat = parse_yosys_stat(synth_dir / "stat.txt")
    pre_stat = parse_yosys_stat(synth_dir / "pre_map_stat.txt")
    log = parse_yosys_log(synth_dir / "yosys_log.txt")

    # 解析 lint 结果
    lint = {}
    lint_candidates = list(synth_dir.glob("*lint*")) + list(Path(".").glob("*lint_result*"))
    if lint_candidates:
        lint = parse_lint_results(lint_candidates[0])

    # --- 基本统计 ---
    print("\n┌─────────────────────────────────────┐")
    print("│  1. 综合统计                        │")
    print("└─────────────────────────────────────┘")

    cell_count = stat.get("cell_count", "N/A")
    area = stat.get("area", "N/A")
    wires = stat.get("wire_count", "N/A")
    mems = stat.get("memory_count", 0)

    print(f"  门级单元数    : {cell_count}")
    print(f"  Wire 数       : {wires}")
    print(f"  存储器数      : {mems}")
    if area != "N/A":
        print(f"  面积估算      : {area}")

    # 映射前后对比
    pre_cells = pre_stat.get("cell_count", None)
    if pre_cells:
        print(f"\n  映射前 (generic): {pre_cells} 单元")
        print(f"  映射后 (PDK)    : {cell_count} 单元")
        if isinstance(cell_count, int) and isinstance(pre_cells, int) and pre_cells > 0:
            reduction = (1 - cell_count / pre_cells) * 100
            print(f"  缩减率          : {reduction:.1f}%")

    # --- 单元分布 ---
    print("\n┌─────────────────────────────────────┐")
    print("│  2. 单元分布                        │")
    print("└─────────────────────────────────────┘")

    cell_types_found = False
    for k, v in sorted(stat.items()):
        if k.endswith("_cells") and v > 0:
            cell_types_found = True
            print(f"  {k.replace('_cells', ''):30s} {v:>8d}")
    if not cell_types_found:
        print("  (无分类统计 — 可能使用了 generic 映射)")

    # --- 警告与错误 ---
    print("\n┌─────────────────────────────────────┐")
    print("│  3. 警告与错误                      │")
    print("└─────────────────────────────────────┘")

    yosys_w = log.get("yosys_warnings", 0)
    yosys_e = log.get("yosys_errors", 0)
    print(f"  Yosys 警告    : {yosys_w}")
    print(f"  Yosys 错误    : {yosys_e}")

    if lint:
        print(f"  Lint 错误     : {lint.get('lint_errors', 'N/A')}")
        print(f"  Lint 警告     : {lint.get('lint_warnings', 'N/A')}")
        print(f"  Lint 风格问题 : {lint.get('lint_style', 'N/A')}")

    # --- QoR 评分 ---
    print("\n┌─────────────────────────────────────┐")
    print("│  4. QoR (Quality of Results) 评分   │")
    print("└─────────────────────────────────────┘")

    score, details = score_qor(stat, log, constraints)
    if score >= 85:
        grade = "🟢 A"
    elif score >= 70:
        grade = "🟡 B"
    elif score >= 50:
        grade = "🟠 C"
    else:
        grade = "🔴 D"

    print(f"\n  总分: {score:.1f} / 100  →  评级: {grade}")
    print("\n  评分明细:")
    for d in details:
        print(f"    {d}")

    # --- 对比模式 ---
    if compare_dir:
        cmp_dir = Path(compare_dir)
        cmp_stat = parse_yosys_stat(cmp_dir / "stat.txt")
        cmp_cells = cmp_stat.get("cell_count", None)
        print("\n┌─────────────────────────────────────┐")
        print("│  5. 对比分析                        │")
        print("└─────────────────────────────────────┘")
        print(f"  当前设计  : {cell_count} 单元")
        print(f"  对比设计  : {cmp_cells} 单元")
        if isinstance(cell_count, int) and isinstance(cmp_cells, int) and cmp_cells > 0:
            delta = (cell_count - cmp_cells) / cmp_cells * 100
            direction = "增加" if delta > 0 else "减少"
            print(f"  差异       : {direction} {abs(delta):.1f}%")
        cur_area = stat.get("area")
        cmp_area = cmp_stat.get("area")
        if cur_area and cmp_area:
            delta_a = (cur_area - cmp_area) / cmp_area * 100
            direction = "增大" if delta_a > 0 else "减小"
            print(f"  面积差异   : {direction} {abs(delta_a):.1f}%")

    # --- 建议 ---
    print("\n┌─────────────────────────────────────┐")
    print("│  6. 优化建议                        │")
    print("└─────────────────────────────────────┘")

    suggestions = []
    if cell_count == "N/A" or (isinstance(cell_count, int) and cell_count == 0):
        suggestions.append("🔴 综合未产生有效网表，请检查 RTL 是否有语法错误。")
    if yosys_w > 10:
        suggestions.append(f"⚡ Yosys 警告较多 ({yosys_w} 条)，建议审查警告原因。")
    if yosys_e > 0:
        suggestions.append(f"🔴 Yosys 报告错误 ({yosys_e} 条)，综合可能不完整。")
    if mems > 8:
        suggestions.append("📐 存储器实例较多，建议确认是否需要 SRAM 编译器生成。")
    if lint.get("lint_errors", 0) > 0:
        suggestions.append(f"🔴 Lint 发现 {lint['lint_errors']} 个错误，请先修复。")

    if not suggestions:
        suggestions.append("✅ 电路评估通过，无明显问题。")
        suggestions.append("💡 进一步：提供 PDK .lib 以获取精确时序和面积数据。")
        suggestions.append("💡 进一步：运行物理设计流程 → 参考 references/physical-design.md")

    for s in suggestions:
        print(f"  {s}")

    print("\n" + "=" * 60)
    print()

    return score, {
        "stat": stat,
        "log": log,
        "lint": lint,
        "score": score,
        "grade": grade,
    }


def main():
    parser = argparse.ArgumentParser(
        description="RISC-V 电路评估 —— 解析综合结果，生成 QoR 报告"
    )
    parser.add_argument("synth_dir", help="综合输出目录（包含 stat.txt / yosys_log.txt）")
    parser.add_argument("--target-freq-mhz", type=float, default=100, help="目标频率 MHz（默认 100）")
    parser.add_argument("--area-budget", type=float, default=None, help="面积预算（单位取决于 PDK）")
    parser.add_argument("--compare", "-c", default=None, help="对比的综合输出目录")
    parser.add_argument("--json", action="store_true", help="额外输出 JSON 格式评估数据")
    args = parser.parse_args()

    synth_dir = Path(args.synth_dir)
    if not synth_dir.exists():
        print(f"[错误] 目录不存在: {args.synth_dir}")
        sys.exit(1)

    constraints = {"target_freq_mhz": args.target_freq_mhz}
    if args.area_budget:
        constraints["area_budget"] = args.area_budget

    score, data = evaluate(
        synth_dir,
        constraints=constraints,
        compare_dir=args.compare,
    )

    if args.json:
        import json
        json_path = synth_dir / "eval_result.json"
        json_out = {
            "score": data["score"],
            "grade": data["grade"],
            "cell_count": data["stat"].get("cell_count"),
            "area": data["stat"].get("area"),
            "yosys_warnings": data["log"].get("yosys_warnings"),
            "yosys_errors": data["log"].get("yosys_errors"),
        }
        json_path.write_text(json.dumps(json_out, indent=2, ensure_ascii=False))
        print(f"  JSON 评估结果已写入: {json_path}")


if __name__ == "__main__":
    main()
