#!/usr/bin/env python3
"""
Verilog/SystemVerilog 模块 testbench 脚手架生成器。

用法：
    python tb_generator.py <module_file.v|module_file.sv> [--sv] [--uvm|--uvm-skeleton] [--clk_period_ns 10] [--output tb_output.sv]

默认输出兼容 Verilog-2001，以确保 Icarus Verilog 兼容。
使用 --sv 输出 SystemVerilog 结构（logic、always_ff 等）。
"""

import re
import sys
import argparse
from pathlib import Path
from datetime import datetime


MODULE_RE = re.compile(
    r"(?:^|\n)\s*module\s+(\w+)"
    r"(?:\s*#\s*\((.*?)\))?"
    r"\s*\((.*?)\);",
    re.DOTALL,
)
CLK_NAME_RE = re.compile(r"clk|clock", re.IGNORECASE)
RST_NAME_RE = re.compile(r"rst|reset|arst|nrst", re.IGNORECASE)


def strip_comments(text):
    """移除注释"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def split_top_level_commas(text):
    """按顶层逗号分割（忽略括号内的逗号）"""
    parts = []
    start = 0
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _normalize_port_piece(piece):
    """规范化端口片段：去除默认值和尾部分号"""
    piece = piece.strip().rstrip(";")
    piece = re.sub(r"\s*=\s*.*$", "", piece)
    return piece


def _parse_port_piece(piece, state):
    """解析单个端口片段，返回端口信息字典或 None"""
    piece = _normalize_port_piece(piece)
    if not piece:
        return None

    direction_match = re.match(r"^(input|output|inout)\b\s*(.*)$", piece)
    if direction_match:
        state["direction"] = direction_match.group(1)
        state["width_msb"] = None
        state["width_lsb"] = None
        piece = direction_match.group(2).strip()

    type_match = re.match(r"^(?:var\s+)?(?:wire|reg|logic|tri)\b\s*(.*)$", piece)
    if type_match:
        piece = type_match.group(1).strip()

    signed_match = re.match(r"^signed\b\s*(.*)$", piece)
    if signed_match:
        piece = signed_match.group(1).strip()

    range_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", piece)
    if range_match:
        range_str = range_match.group(1)
        range_parts = [p.strip() for p in range_str.split(":")]
        if len(range_parts) == 2:
            state["width_msb"] = range_parts[0]
            state["width_lsb"] = range_parts[1]
        piece = range_match.group(2).strip()

    name_match = re.match(r"^([a-zA-Z_]\w*)\b", piece)
    if not name_match or not state.get("direction"):
        return None

    name = name_match.group(1)
    return {
        "direction": state["direction"],
        "width_msb": state.get("width_msb"),
        "width_lsb": state.get("width_lsb"),
        "name": name,
        "is_clock": bool(CLK_NAME_RE.search(name)),
        "is_reset": bool(RST_NAME_RE.search(name)),
    }


def extract_ports(text):
    """从模块源代码中提取端口列表"""
    text = strip_comments(text)
    ports = []
    seen = set()

    module_match = MODULE_RE.search(text)
    if module_match:
        state = {"direction": None, "width_msb": None, "width_lsb": None}
        for piece in split_top_level_commas(module_match.group(3) or ""):
            parsed = _parse_port_piece(piece, state)
            if parsed and parsed["name"] not in seen:
                ports.append(parsed)
                seen.add(parsed["name"])

    decl_re = re.compile(r"^\s*(input|output|inout)\b.*?;", re.MULTILINE)
    for decl in decl_re.finditer(text):
        state = {"direction": None, "width_msb": None, "width_lsb": None}
        for piece in split_top_level_commas(decl.group(0)):
            parsed = _parse_port_piece(piece, state)
            if parsed and parsed["name"] not in seen:
                ports.append(parsed)
                seen.add(parsed["name"])

    return ports


def _is_numeric(expr):
    """检查表达式是否为纯整数（非 DATA_WIDTH-1 等参数表达式）"""
    try:
        int(expr)
        return True
    except ValueError:
        return False


def _calc_width(width_msb, width_lsb):
    """计算位宽，若表达式包含参数则返回 None"""
    if not _is_numeric(width_msb) or not _is_numeric(width_lsb):
        return None
    return int(width_msb) - int(width_lsb) + 1


def _zero_val(width_msb, width_lsb, is_sv):
    """返回 Verilog-2001 或 SystemVerilog 零值字面量"""
    if width_msb is None:
        return "1'b0"
    width = _calc_width(width_msb, width_lsb)
    if width is None:
        return "0"  # 不定长零值；综合器/仿真器会扩展
    if width <= 1:
        return "1'b0"
    if is_sv:
        return str(width) + "'b0"
    return "{" + str(width) + "{1'b0}}"


def _x_val(width_msb, width_lsb):
    """返回用于边界情况注入的 X 字面量"""
    if width_msb is None:
        return "1'bx"
    width = _calc_width(width_msb, width_lsb)
    if width is None:
        return "1'bx"
    if width <= 1:
        return "1'bx"
    return "{" + str(width) + "{1'bx}}"


def _z_val(width_msb, width_lsb):
    """返回用于边界情况注入的 Z 字面量"""
    if width_msb is None:
        return "1'bz"
    width = _calc_width(width_msb, width_lsb)
    if width is None:
        return "1'bz"
    if width <= 1:
        return "1'bz"
    return "{" + str(width) + "{1'bz}}"


def _max_val(width_msb, width_lsb):
    """返回全 1 字面量，用于最大值边界测试"""
    if width_msb is None:
        return "1'b1"
    width = _calc_width(width_msb, width_lsb)
    if width is None:
        return "1'b1"
    if width <= 1:
        return "1'b1"
    return "{" + str(width) + "{1'b1}}"


def generate_simple_tb(module_name, ports, clk_period_ns, is_sv):
    """生成简易定向 testbench（Verilog-2001 或 SystemVerilog）"""
    tb_name = "tb_" + module_name
    out = []
    out.append("// 自动生成的 testbench —— " + module_name)
    out.append("// 风格：" + ("SystemVerilog" if is_sv else "Verilog-2001"))
    out.append("// 日期：" + datetime.now().isoformat())
    out.append("`timescale 1ns / 1ps")
    out.append("")
    out.append("module " + tb_name + ";")
    out.append("")
    out.append("    localparam CLK_PERIOD = " + str(clk_period_ns) + ";")
    out.append("")
    for p in ports:
        if p["is_clock"]:
            continue
        width_decl = ""
        if p["width_msb"] is not None:
            width_decl = " [" + p["width_msb"] + ":" + p["width_lsb"] + "]"
        if is_sv:
            dtype = "logic"
        else:
            dtype = "reg" if p["direction"] != "output" else "wire"
        out.append("    " + dtype + width_decl + " " + p["name"] + ";")
    clk_ports = [p for p in ports if p["is_clock"]]
    rst_ports = [p for p in ports if p["is_reset"]]
    other_ports = [p for p in ports if not p["is_clock"] and not p["is_reset"]]
    for cp in clk_ports:
        if is_sv:
            out.append("    logic " + cp["name"] + " = 1'b0;")
        else:
            out.append("    reg   " + cp["name"] + " = 1'b0;")
    out.append("")
    for cp in clk_ports:
        out.append("    always #(CLK_PERIOD/2) " + cp["name"] + " = ~" + cp["name"] + ";")
    out.append("")
    if rst_ports:
        rp = rst_ports[0]
        rst_active = "1'b0" if "_n" in rp["name"] or "_N" in rp["name"] else "1'b1"
        out.append("    task apply_reset;")
        out.append("        begin")
        out.append("            " + rp["name"] + " = " + rst_active + ";")
        out.append("            #(CLK_PERIOD*3);")
        rst_inactive = "1'b1" if rst_active == "1'b0" else "1'b0"
        out.append("            " + rp["name"] + " = " + rst_inactive + ";")
        out.append("            #(CLK_PERIOD*2);")
        out.append("        end")
        out.append("    endtask")
        out.append("")
    out.append("    // DUT 实例化")
    out.append("    " + module_name + " u_dut (")
    port_conns = ["        ." + p["name"] + "(" + p["name"] + ")" for p in ports]
    out.append(",\n".join(port_conns))
    out.append("    );")
    out.append("")
    out.append("    initial begin")
    out.append("        // 配置波形 dump")
    out.append("        $dumpfile(\"" + tb_name + "_waves.vcd\");")
    out.append("        $dumpvars(0, " + tb_name + ");")
    out.append("")
    out.append("        // 初始化输入为已知值")
    for p in other_ports:
        if p["direction"] in ("input", "inout"):
            out.append("        " + p["name"] + " = " + _zero_val(p["width_msb"], p["width_lsb"], is_sv) + ";")
    out.append("")

    if rst_ports:
        out.append("        // 执行复位")
        out.append("        apply_reset;")
    out.append("")
    out.append("        // TODO：在此添加定向激励")
    out.append("        // 正常情况写入示例：")
    for p in other_ports[:3]:
        if p["direction"] in ("input", "inout"):
            has_wide = p["width_msb"] is not None and _is_numeric(p["width_msb"]) and int(p["width_msb"]) >= 7
            test_val = "8'hAA" if has_wide else "1'b1"
            out.append("        // " + p["name"] + " = " + test_val + "; #(CLK_PERIOD*2);")
    out.append("")
    out.append("        // 边界测试：X/Z 注入及全 1 最大值溢出")
    for p in other_ports:
        if p["direction"] in ("input", "inout"):
            xv = _x_val(p["width_msb"], p["width_lsb"])
            zv = _z_val(p["width_msb"], p["width_lsb"])
            mv = _max_val(p["width_msb"], p["width_lsb"])
            out.append("        " + p["name"] + " = " + xv + "; #(CLK_PERIOD); // X 态注入")
            out.append("        " + p["name"] + " = " + zv + "; #(CLK_PERIOD); // Z 态注入")
            out.append("        " + p["name"] + " = " + mv + "; #(CLK_PERIOD); // 全 1 / 最大值")
    out.append("")
    out.append("        // 运行并结束")
    out.append("        #(CLK_PERIOD*20);")
    out.append("        $display(\"仿真完成，时间 %0t\", $time);")
    out.append("        $finish;")
    out.append("    end")
    out.append("")
    out.append("    initial begin")
    out.append("        #(CLK_PERIOD*1000);")
    out.append("        $display(\"错误：仿真超时，时间 %0t\", $time);")
    out.append("        $finish;")
    out.append("    end")
    out.append("")
    if is_sv:
        out.append("    // 覆盖率")
        out.append("    // TODO：添加功能覆盖率和断言")
        out.append("")
    out.append("endmodule // " + tb_name)
    out.append("")
    return "\n".join(out)


def generate_uvm_tb(module_name, ports, clk_period_ns):
    """生成 UVM 骨架 testbench"""
    tb_name = "tb_" + module_name
    if_name = module_name.lower() + "_if"
    clk_ports = [p for p in ports if p["is_clock"]]
    rst_ports = [p for p in ports if p["is_reset"]]
    default_rst = rst_ports[0]["name"] if rst_ports else "rst_n"
    lines = []
    lines.append("// 自动生成的 UVM 骨架 —— " + module_name)
    lines.append("// 按需扩展 driver、monitor、scoreboard、agent 和 environment 组件。")
    lines.append("`include \"uvm_macros.svh\"")
    lines.append("import uvm_pkg::*;")
    lines.append("`timescale 1ns / 1ps")
    lines.append("")
    lines.append("interface " + if_name + "();")
    if clk_ports:
        for p in clk_ports:
            lines.append("    logic " + p["name"] + " = 1'b0;")
    else:
        lines.append("    logic clk = 1'b0;")
    if rst_ports:
        for p in rst_ports:
            lines.append("    logic " + p["name"] + ";")
    else:
        lines.append("    logic rst_n;")
    for p in ports:
        if p["is_clock"] or p["is_reset"]:
            continue
        w = ""
        if p["width_msb"] is not None:
            w = " [" + p["width_msb"] + ":" + p["width_lsb"] + "]"
        lines.append("    logic" + w + " " + p["name"] + "; // " + p["direction"])
    if clk_ports:
        for p in clk_ports:
            lines.append("    always #(" + str(clk_period_ns) + "/2) " + p["name"] + " = ~" + p["name"] + ";")
    else:
        lines.append("    always #(" + str(clk_period_ns) + "/2) clk = ~clk;")
    lines.append("endinterface")
    lines.append("")
    cls = module_name.lower() + "_seq_item"
    lines.append("class " + cls + " extends uvm_sequence_item;")
    lines.append("    `uvm_object_utils(" + cls + ")")
    lines.append("")
    for p in ports:
        if p["is_clock"] or p["is_reset"]:
            continue
        w = ""
        if p["width_msb"] is not None:
            w = " [" + p["width_msb"] + ":" + p["width_lsb"] + "]"
        lines.append("    rand logic" + w + " " + p["name"] + "; // " + p["direction"])
    lines.append("")
    lines.append("    function new(string name = \"\");")
    lines.append("        super.new(name);")
    lines.append("    endfunction")
    lines.append("endclass")
    lines.append("")
    lines.append("class base_test extends uvm_test;")
    lines.append("    `uvm_component_utils(base_test)")
    lines.append("    virtual " + if_name + " vif;")
    lines.append("")
    lines.append("    function new(string name, uvm_component parent);")
    lines.append("        super.new(name, parent);")
    lines.append("    endfunction")
    lines.append("    function void build_phase(uvm_phase phase);")
    lines.append("        super.build_phase(phase);")
    lines.append("        if (!uvm_config_db #(virtual " + if_name + ")::get(this, \"\", \"vif\", vif)) begin")
    lines.append("            `uvm_fatal(\"NOVIF\", \"Virtual interface not configured\")")
    lines.append("        end")
    lines.append("    endfunction")
    lines.append("    task run_phase(uvm_phase phase);")
    lines.append("        phase.raise_objection(this);")
    lines.append("        `uvm_info(\"BASE_TEST\", \"UVM 骨架已启动；在此添加 sequence 和检查\", UVM_LOW)")
    lines.append("        #(" + str(clk_period_ns * 10) + ");")
    lines.append("        phase.drop_objection(this);")
    lines.append("    endtask")
    lines.append("endclass")
    lines.append("")
    lines.append("module " + tb_name + ";")
    lines.append("    " + if_name + " u_if();")
    lines.append("")
    lines.append("    " + module_name + " u_dut (")
    pconns = []
    for p in ports:
        pconns.append("        ." + p["name"] + "(u_if." + p["name"] + ")")
    lines.append(",\n".join(pconns))
    lines.append("    );")
    lines.append("")
    lines.append("    initial begin")
    rst_active = "1'b0" if "_n" in default_rst or "_N" in default_rst else "1'b1"
    rst_inactive = "1'b1" if rst_active == "1'b0" else "1'b0"
    lines.append("        u_if." + default_rst + " = " + rst_active + ";")
    lines.append("        #20;")
    lines.append("        u_if." + default_rst + " = " + rst_inactive + ";")
    lines.append("")
    vif_type = "virtual " + if_name
    lines.append("        uvm_config_db #(" + vif_type + ")::set(null, \"*\", \"vif\", u_if);")
    lines.append("        run_test(\"base_test\");")
    lines.append("    end")
    lines.append("")
    lines.append("    initial begin")
    lines.append("        $dumpfile(\"" + tb_name + "_waves.vcd\");")
    lines.append("        $dumpvars(0, " + tb_name + ");")
    lines.append("    end")
    lines.append("")
    lines.append("    initial begin")
    lines.append("        #(1000*10);")
    lines.append("        $display(\"错误：仿真超时\");")
    lines.append("        $finish;")
    lines.append("    end")
    lines.append("endmodule // " + tb_name)
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Testbench 脚手架生成器")
    parser.add_argument("file", help="RTL 模块文件（.v 或 .sv）")
    parser.add_argument("--sv", action="store_true", help="输出 SystemVerilog 结构（logic、always_ff 等）")
    parser.add_argument("--uvm", action="store_true", help="生成 UVM 骨架 testbench")
    parser.add_argument("--uvm-skeleton", action="store_true", help="生成 UVM 骨架 testbench")
    parser.add_argument("--clk_period_ns", type=int, default=10, help="时钟周期，单位 ns")
    parser.add_argument("--output", "-o", help="输出文件路径")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print("错误：文件未找到: " + args.file)
        sys.exit(1)

    content = path.read_text()
    is_sv = args.sv

    m = MODULE_RE.search(content)
    if not m:
        print("错误：在 " + args.file + " 中未找到模块声明")
        sys.exit(2)

    module_name = m.group(1)
    port_block = m.group(3) or ""
    ports = extract_ports(content)
    if not ports:
        ports = extract_ports(port_block)

    print("找到模块：" + module_name)
    print("  端口数：" + str(len(ports)))
    for p in ports:
        w = "[" + p["width_msb"] + ":" + p["width_lsb"] + "]" if p["width_msb"] else ""
        print("    " + p["direction"] + " " + w + " " + p["name"])

    use_uvm = args.uvm or args.uvm_skeleton

    if use_uvm:
        tb_content = generate_uvm_tb(module_name, ports, args.clk_period_ns)
    else:
        tb_content = generate_simple_tb(module_name, ports, args.clk_period_ns, is_sv)

    out_path = Path(args.output) if args.output else Path("tb_" + module_name + ".v")
    out_path.write_text(tb_content)
    print("\nTestbench 已写入：" + str(out_path))
    print("风格：" + ("UVM 骨架" if use_uvm else ("SystemVerilog" if is_sv else "Verilog-2001")))


if __name__ == "__main__":
    main()
